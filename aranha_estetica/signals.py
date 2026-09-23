"""Signals de model — efeitos que valem para QUALQUER caminho de gravacao.

Divisao de responsabilidades (cada efeito tem UM mecanismo, sem duplicar):
  - aqui (post_save de Atendimento): contador de faltas, debito de pacote
    (nunca de retorno gratuito; validade pela data da sessao),
    lista de espera (vaga liberada) e push/gcal de novo agendamento;
  - EventBus (services/*): comissao, cashback e retorno obrigatorio.
"""
from django.db import transaction
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from .models import Atendimento, CompraPacote, ConsumoSessao, Configuracao
import logging

logger = logging.getLogger(__name__)

# Status que ocupam horario na agenda (liberam vaga ao sair deles).
_STATUS_ATIVOS = ('PENDENTE', 'AGENDADO', 'CONFIRMADO')
# Saidas que devolvem o horario para a lista de espera.
_STATUS_LIBERAM_VAGA = ('CANCELADO', 'REAGENDADO')


@receiver([post_save, post_delete], sender=Configuracao)
def invalidar_cache_branding(sender, instance, **kwargs):
    """Zera o cache de marca/contatos (utils/branding) quando Configuracao muda."""
    from .utils.branding import invalidar_cache
    invalidar_cache()


@receiver(pre_save, sender=Atendimento)
def capturar_status_anterior(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = Atendimento.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
        except Atendimento.DoesNotExist:
            logger.warning('capturar_status_anterior: Atendimento pk=%s nao encontrado no pre_save', instance.pk)
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender=Atendimento)
def processar_mudanca_status(sender, instance, created, **kwargs):
    status_atual = instance.status
    status_anterior = getattr(instance, '_old_status', None)

    if created:
        # I/O externo (push + gcal) so apos o commit: nao bloqueia/atrasa a
        # transacao do request e nao dispara para um atendimento que sofreu
        # rollback. Mantem-se best-effort (except amplo nao quebra o fluxo).
        def _push_profissional():
            try:
                user = getattr(instance.profissional, 'usuario', None)
                if user:
                    from .services.push import send_push_to_user
                    from .utils.datas import fmt_local
                    cliente_nome = instance.cliente.nome if instance.cliente_id else 'Cliente'
                    proc_nome = instance.procedimento.nome if instance.procedimento_id else 'Atendimento'
                    data_fmt = fmt_local(instance.data_hora_inicio, '%d/%m %H:%M')
                    send_push_to_user(user, {
                        'head': 'Novo agendamento',
                        'body': f'{cliente_nome} - {proc_nome} em {data_fmt}',
                        'url': '/profissional/',
                    })
            except Exception as e:
                logger.exception('push profissional falhou: %s', e)

        def _sync_gcal():
            try:
                from .services.gcal import push_atendimento, gcal_disponivel
                if gcal_disponivel() and instance.profissional.gcal_refresh_token:
                    push_atendimento(instance)
            except Exception as e:
                logger.exception('gcal push falhou: %s', e)

        transaction.on_commit(_push_profissional)
        transaction.on_commit(_sync_gcal)

    if status_atual == status_anterior:
        return

    # REGRA: LISTA DE ESPERA — cancelamento/reagendamento libera o horario.
    # A selecao roda nesta transacao; e-mail/WhatsApp saem no on_commit do service.
    if status_atual in _STATUS_LIBERAM_VAGA and status_anterior in _STATUS_ATIVOS:
        try:
            from .services.lista_espera_service import ListaEsperaService
            with transaction.atomic():  # savepoint: erro aqui nao aborta a transacao de quem cancelou
                ListaEsperaService.notificar_compativeis(instance)
        except Exception as e:  # noqa: BLE001 — aviso de vaga nunca quebra o cancelamento
            logger.exception('lista_espera_falhou: %s', e)

    # REGRA: REGISTRO DE FALTA — 3-strike system
    if status_atual == 'FALTOU' and status_anterior in _STATUS_ATIVOS:
        instance.cliente.registrar_falta()
        logger.info(f"[FALTA] Cliente {instance.cliente.pk} — faltas: {instance.cliente.faltas_consecutivas}")

    # REGRA: REALIZADO — resetar faltas + debitar pacote
    if status_atual == 'REALIZADO':
        # Reset faltas consecutivas
        instance.cliente.resetar_faltas()

        # Debitar sessao de pacote — atomico + select_for_update serializa
        # debitos concorrentes do mesmo cliente (evita over-debit no TOCTOU
        # entre o .count() de sessoes feitas e o ConsumoSessao.create()).
        # - Retorno gratuito NAO consome sessao paga (REGRAS §7).
        # - Validade conferida na DATA DA SESSAO (local), nao no dia em que se
        #   marca REALIZADO: sessao feita ate o ultimo dia debita mesmo se o
        #   job ja marcou o pacote EXPIRADO; sessao apos a validade nao debita.
        if not instance.eh_retorno and not hasattr(instance, 'sessao_pacote_vinculada'):
            from django.db.models import F, Q
            from .utils.datas import data_local
            data_sessao = data_local(instance.data_hora_inicio)
            with transaction.atomic():
                pacotes_validos = CompraPacote.objects.select_for_update().filter(
                    cliente=instance.cliente,
                    status__in=('ATIVO', 'EXPIRADO'),
                ).filter(
                    Q(data_expiracao__isnull=True) | Q(data_expiracao__gte=data_sessao)
                ).order_by(F('data_expiracao').asc(nulls_last=True), 'criado_em')

                for pc in pacotes_validos:
                    itens = pc.pacote.itens.filter(procedimento=instance.procedimento)
                    if itens.exists():
                        item = itens.first()
                        sessoes_ja_feitas = pc.sessoes_realizadas.filter(
                            atendimento__procedimento=instance.procedimento
                        ).count()
                        if sessoes_ja_feitas < item.quantidade_sessoes:
                            ConsumoSessao.objects.create(
                                compra_pacote=pc,
                                atendimento=instance
                            )
                            logger.info(f"[PACOTE] Sessao {sessoes_ja_feitas + 1}/{item.quantidade_sessoes} debitada do pacote {pc.pk}")
                            pc.verificar_finalizacao()
                            break
