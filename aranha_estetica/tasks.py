"""
Celery Tasks — Plataforma de Clinicas

Estrategia de canais (aprovada 2026-04-18):
  WhatsApp:  Confirmacao D-1, NPS pos-atendimento (apenas 2 templates)
  Email:     OTP, confirmacao, cancelamento, fila, pacotes, aniversario,
             promocoes, termos, alertas admin (detrator NPS)
  SMS:       OTP (primario; email fallback) — integrado via utils/sms.py
"""
import os
import secrets
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from .models import Atendimento, ListaEspera, AvaliacaoNPS
import logging

logger = logging.getLogger(__name__)

CLINIC_NAME = os.environ.get('CLINIC_NAME', 'Jaqueline Aranha Estética')


# Workflow engine removido na remodelagem v2.1 fase 1d — regras configuraveis
# por UI eram over-engineering p/ 1 clinica; reacoes vivem em services/handlers.


# ═══════════════════════════════════════
#  WHATSAPP — Confirmacao D-1
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_enviar_lembrete_dia_seguinte(self):
    """Envia confirmacao D-1 via WhatsApp (template) para agendamentos de amanha."""
    try:
        from .utils.whatsapp import enviar_confirmacao_d1
        from .models import Notificacao

        amanha = timezone.now().date() + timedelta(days=1)
        agendamentos = Atendimento.objects.filter(
            data_hora_inicio__date=amanha,
            status='AGENDADO'
        ).select_related('cliente', 'profissional', 'procedimento')

        logger.info(f"[JOB LEMBRETE] {agendamentos.count()} agendamentos para amanha ({amanha}).")

        enviados = 0
        for agendamento in agendamentos:
            if not agendamento.cliente.telefone:
                continue
            ja_enviou = Notificacao.objects.filter(
                atendimento=agendamento,
                tipo='LEMBRETE',
                status='ENVIADO'
            ).exists()
            if ja_enviou:
                continue
            # Falha pontual nao re-dispara o batch (retry reenviaria a quem ja
            # recebeu); o guard ja_enviou acima ja garante idempotencia.
            try:
                notif = enviar_confirmacao_d1(agendamento)
                if notif and notif.status == 'ENVIADO':
                    enviados += 1
            except Exception as e:
                logger.error(
                    '[JOB LEMBRETE] Falha ao enviar para atendimento %s: %s',
                    agendamento.pk, e, exc_info=True,
                )

        logger.info(f"[JOB LEMBRETE] {enviados} lembretes enviados com sucesso.")
        return f'{enviados} lembretes enviados'
    except Exception as exc:
        logger.exception('Erro em job_enviar_lembrete_dia_seguinte: %s', exc)
        raise self.retry(exc=exc) from exc


# ═══════════════════════════════════════
#  WHATSAPP — NPS 24h pos atendimento
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_pesquisa_satisfacao_24h(self):
    """Envia NPS por WhatsApp 24h apos atendimento REALIZADO.

    Requer consent_whatsapp_nps=True do cliente. Sem consent, ignora.
    """
    try:
        from .utils.whatsapp import enviar_nps_whatsapp, SITE_URL
        from .models import Notificacao

        site_url = SITE_URL.rstrip('/')

        limite = timezone.now() - timedelta(days=1)
        # exclui so quem ja tem NPS pendente/enviado (FALHOU pode re-tentar);
        # distinct() evita multiplicacao de linhas pelo join da FK reversa.
        agendamentos = Atendimento.objects.filter(
            status='REALIZADO',
            data_hora_fim__lte=limite,
            avaliacaonps__isnull=True,
            cliente__consent_whatsapp_nps=True,
        ).exclude(
            notificacao__tipo='NPS',
            notificacao__status__in=['PENDENTE', 'ENVIADO'],
        ).select_related('cliente', 'procedimento').distinct()

        logger.info(f"[JOB NPS] {agendamentos.count()} atendimentos sem avaliacao e com consent.")

        enviados = 0
        for agendamento in agendamentos:
            if not agendamento.cliente.telefone:
                logger.warning(f"[NPS WA] Cliente {agendamento.cliente.pk} sem telefone — NPS nao enviado")
                continue

            token = secrets.token_urlsafe(32)
            notif = Notificacao.objects.create(
                atendimento=agendamento,
                tipo='NPS',
                canal='WHATSAPP',
                token=token,
                status='PENDENTE',
            )
            nps_url = f"{site_url}/nps/{token}/"

            # Status reflete o resultado real do envio (nao deixa orfa PENDENTE):
            # ENVIADO em sucesso, FALHOU em falha (e ai e re-tentavel no proximo run).
            if enviar_nps_whatsapp(agendamento, nps_url, token):
                notif.status = 'ENVIADO'
                notif.save(update_fields=['status'])
                enviados += 1
            else:
                notif.status = 'FALHOU'
                notif.save(update_fields=['status'])

        logger.info(f"[JOB NPS] {enviados} NPS enviados via WhatsApp.")
        return f'{enviados} NPS enviados'
    except Exception as exc:
        logger.exception('Erro em job_pesquisa_satisfacao_24h: %s', exc)
        raise self.retry(exc=exc) from exc


# ═══════════════════════════════════════
#  EMAIL — Alerta detrator NPS (admin)
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_alerta_detrator_nps(self):
    """Alerta admin por EMAIL quando NPS <= 6 (detrator)."""
    try:
        from django.core.mail import send_mail
        from .models import Configuracao

        detratores = list(AvaliacaoNPS.objects.filter(
            nota__lte=6,
            alerta_enviado=False
        ).select_related('atendimento__cliente', 'atendimento__procedimento'))

        if not detratores:
            return

        config = Configuracao.objects.filter(chave='email_admin').first()
        email_admin = config.valor if config else os.environ.get('ADMIN_EMAIL', '')

        if not email_admin:
            logger.warning('nps_detrator_sem_email_admin')
            for avaliacao in detratores:
                logger.warning(
                    'nps_detrator_sem_email_admin_cliente',
                    extra={
                        'cliente_id': avaliacao.atendimento.cliente_id,
                        'nota': avaliacao.nota,
                    },
                )
            return

        default_from = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@clinica.com.br')

        for avaliacao in detratores:
            at = avaliacao.atendimento
            assunto = f'[{CLINIC_NAME}] ALERTA NPS — {at.cliente.nome} nota {avaliacao.nota}'
            corpo = (
                f'Cliente: {at.cliente.nome}\n'
                f'Procedimento: {at.procedimento.nome}\n'
                f'Profissional: {at.profissional.nome if at.profissional_id else "-"}\n'
                f'Data atendimento: {at.data_hora_inicio.strftime("%d/%m/%Y %H:%M")}\n'
                f'Nota: {avaliacao.nota}/10\n'
                f'Comentario: {avaliacao.comentario or "(sem comentario)"}\n'
            )
            try:
                send_mail(
                    subject=assunto,
                    message=corpo,
                    from_email=default_from,
                    recipient_list=[email_admin],
                    fail_silently=False,
                )
                avaliacao.alerta_enviado = True
                avaliacao.save(update_fields=['alerta_enviado'])
                logger.warning(
                    'nps_detrator_alerta_enviado',
                    extra={
                        'cliente_id': at.cliente_id,
                        'nota': avaliacao.nota,
                    },
                )
            except Exception as e:
                # Nao marca alerta_enviado -> re-tentado no proximo run (idempotente).
                logger.error('nps_detrator_falha_alerta', extra={'error': str(e)}, exc_info=True)
    except Exception as exc:
        logger.exception('Erro em job_alerta_detrator_nps: %s', exc)
        raise self.retry(exc=exc) from exc


# ═══════════════════════════════════════
#  EMAIL — Fila de espera
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_notificar_fila_espera(self, procedimento_id: int, data_livre_str: str):
    """Notifica interessados da fila de espera por EMAIL."""
    from .utils.email import enviar_fila_espera_email

    try:
        parsed = timezone.datetime.fromisoformat(data_livre_str)
    except (ValueError, TypeError) as exc:
        # Entrada malformada e erro permanente: re-tentar nao resolve.
        logger.error('[JOB ESPERA] data_livre_str invalida (%r): %s', data_livre_str, exc)
        return

    # Toma a data no fuso local da clinica (um datetime aware as 23h local
    # poderia cair no dia seguinte em UTC, gerando match de data errado).
    if timezone.is_aware(parsed):
        parsed = timezone.localtime(parsed)
    data_livre = parsed.date()

    try:
        logger.info(f"[JOB ESPERA] Vaga liberada para procedimento {procedimento_id} na data {data_livre}.")

        interessados = ListaEspera.objects.filter(
            procedimento_id=procedimento_id,
            data_desejada=data_livre,
            notificado=False
        ).select_related('cliente', 'procedimento').order_by('criado_em')

        for espera in interessados:
            if not espera.cliente.email:
                logger.warning(
                    '[JOB ESPERA] Cliente %s sem email — nao notificado',
                    espera.cliente.pk,
                )
                continue
            # Falha pontual de 1 destinatario nao deve re-disparar o batch
            # inteiro (retry reenviaria aos ja processados). Loga e segue.
            try:
                enviar_fila_espera_email(espera.cliente.email, {
                    'nome': espera.cliente.nome,
                    'procedimento': espera.procedimento.nome,
                    'data': data_livre.strftime('%d/%m/%Y'),
                })
                espera.notificado = True
                espera.save(update_fields=['notificado'])
            except Exception as e:
                logger.error(
                    '[JOB ESPERA] Falha ao notificar cliente %s: %s',
                    espera.cliente.pk, e, exc_info=True,
                )
    except Exception as exc:
        logger.exception('Erro em job_notificar_fila_espera: %s', exc)
        raise self.retry(exc=exc) from exc


# ═══════════════════════════════════════
#  EMAIL — Pacotes expirando
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_verificar_pacotes_expirando(self):
    """Notifica clientes com pacotes expirando em 7 ou 1 dia — por EMAIL."""
    try:
        from django.db.models import Count
        from .utils.email import enviar_pacote_expirando_email
        from .models import CompraPacote

        hoje = timezone.now().date()

        for dias in [7, 1]:
            data_alvo = hoje + timedelta(days=dias)
            pacotes = CompraPacote.objects.filter(
                status='ATIVO',
                data_expiracao=data_alvo
            ).select_related('cliente', 'pacote').prefetch_related('pacote__itens')

            for pc in pacotes:
                # Contagem de sessoes consumidas por procedimento numa unica
                # query agregada (evita N+1 de .filter().count() por item).
                feitas_por_proc = {
                    row['atendimento__procedimento']: row['c']
                    for row in pc.sessoes_realizadas
                    .values('atendimento__procedimento')
                    .annotate(c=Count('id'))
                }
                sessoes_restantes = sum(
                    max(0, item.quantidade_sessoes - feitas_por_proc.get(item.procedimento_id, 0))
                    for item in pc.pacote.itens.all()
                )

                if sessoes_restantes <= 0:
                    continue
                if not pc.cliente.email:
                    logger.warning(
                        '[PACOTE EXPIRANDO] Cliente %s sem email — nao notificado',
                        pc.cliente.pk,
                    )
                    continue
                # Falha de 1 destinatario nao re-dispara o batch inteiro.
                try:
                    enviar_pacote_expirando_email(pc.cliente.email, {
                        'nome': pc.cliente.nome,
                        'pacote': pc.pacote.nome,
                        'dias': dias,
                        'sessoes_restantes': sessoes_restantes,
                    })
                    logger.info(f"[PACOTE EXPIRANDO] Cliente {pc.cliente.pk} — {dias} dias restantes")
                except Exception as e:
                    logger.error(
                        '[PACOTE EXPIRANDO] Falha ao notificar cliente %s: %s',
                        pc.cliente.pk, e, exc_info=True,
                    )
    except Exception as exc:
        logger.exception('Erro em job_verificar_pacotes_expirando: %s', exc)
        raise self.retry(exc=exc) from exc


# ═══════════════════════════════════════
#  EMAIL — Aniversario
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_aniversario_clientes(self):
    """Envia email + WhatsApp de aniversario com desconto.

    Email: requer consent_email_marketing=True.
    WhatsApp: requer consent_whatsapp_confirmacao=True (template utility).
    """
    try:
        from .constants import DESCONTO_ANIVERSARIO_PERCENTUAL
        from .models import Cliente

        hoje = timezone.now().date()
        aniversariantes = Cliente.objects.filter(
            data_nascimento__month=hoje.month,
            data_nascimento__day=hoje.day,
            ativo=True,
        )

        emails_enviados = 0
        whatsapps_enviados = 0

        for cliente in aniversariantes:
            dados = {'nome': cliente.nome, 'desconto': DESCONTO_ANIVERSARIO_PERCENTUAL}

            # Email (requer consent marketing). Falha pontual nao re-dispara o
            # batch (retry reenviaria aos ja parabenizados) — loga e segue.
            if cliente.email and cliente.consent_email_marketing:
                try:
                    from .utils.email import enviar_aniversario_email
                    enviar_aniversario_email(cliente.email, dados)
                    emails_enviados += 1
                except Exception as e:
                    logger.error(
                        'aniversario_email_falha',
                        extra={'cliente_id': cliente.pk, 'error': str(e)},
                    )

            # WhatsApp (requer consent confirmacao + telefone).
            # _enviar_aniversario_whatsapp ja e best-effort (try/except interno).
            if cliente.telefone and cliente.consent_whatsapp_confirmacao:
                _enviar_aniversario_whatsapp(cliente, DESCONTO_ANIVERSARIO_PERCENTUAL)
                whatsapps_enviados += 1

        logger.info(
            'aniversario_disparado',
            extra={
                'aniversariantes': aniversariantes.count(),
                'emails': emails_enviados,
                'whatsapps': whatsapps_enviados,
            },
        )
    except Exception as exc:
        logger.exception('aniversario_erro')
        raise self.retry(exc=exc) from exc


def _enviar_aniversario_whatsapp(cliente, desconto_percentual: int) -> None:
    """Best-effort WhatsApp template aniversario_estetica."""
    try:
        from .utils.whatsapp import enviar_template_whatsapp
        components = [{
            'type': 'body',
            'parameters': [
                {'type': 'text', 'text': cliente.nome},
                {'type': 'text', 'text': str(desconto_percentual)},
            ],
        }]
        enviar_template_whatsapp(cliente.telefone, 'aniversario_estetica', components=components)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            'aniversario_wa_falha',
            extra={'cliente_id': cliente.pk, 'error': str(exc)},
        )


# ═══════════════════════════════════════
#  EMAIL — Promocao mensal (opt-in)
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_promocao_mensal(self, assunto: str, corpo_html_partial: str, cupom: str = None, validade_dias: int = 30):
    """Envia email promocional para clientes com consent_email_marketing=True.

    Parametros:
      assunto: subject do email
      corpo_html_partial: snippet HTML inserido no template base de promocao
      cupom: codigo de cupom (opcional)
      validade_dias: dias ate expiracao do cupom (default 30)
    """
    try:
        from .utils.email import enviar_promocao_email
        from .models import Cliente

        destinatarios = Cliente.objects.filter(
            ativo=True,
            email__isnull=False,
            consent_email_marketing=True,
        ).exclude(email='')

        logger.info(f"[JOB PROMOCAO] {destinatarios.count()} destinatario(s) com consent.")

        validade = (timezone.now().date() + timedelta(days=validade_dias)).strftime('%d/%m/%Y')
        enviados = 0
        for cliente in destinatarios:
            # Roteia pelo helper dedicado: aplica bleach em corpo_html (anti-XSS)
            # e injeta header List-Unsubscribe (RFC 8058) por ser marketing.
            # Falha pontual nao re-dispara o batch (retry reenviaria a todos).
            try:
                ok = enviar_promocao_email(
                    cliente.email,
                    {
                        'nome': cliente.nome,
                        'corpo_html': corpo_html_partial,
                        'cupom': cupom,
                        'validade': validade,
                    },
                    unsub_token=cliente.token_descadastro,
                    assunto=assunto,
                )
                if ok:
                    enviados += 1
            except Exception as e:
                logger.error(
                    '[JOB PROMOCAO] Falha ao enviar para cliente %s: %s',
                    cliente.pk, e, exc_info=True,
                )
        logger.info(f"[JOB PROMOCAO] {enviados} emails enviados.")
        return f'{enviados} promocoes enviadas'
    except Exception as exc:
        logger.exception('Erro em job_promocao_mensal: %s', exc)
        raise self.retry(exc=exc) from exc


# ═══════════════════════════════════════
#  SISTEMA — Expirar pacotes
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_expirar_pacotes(self):
    """Expira pacotes vencidos automaticamente."""
    try:
        from .models import CompraPacote

        hoje = timezone.now().date()
        expirados = CompraPacote.objects.filter(
            status='ATIVO',
            data_expiracao__lt=hoje
        ).update(status='EXPIRADO')

        if expirados:
            logger.info(f"[PACOTE] {expirados} pacote(s) expirado(s) automaticamente.")
    except Exception as exc:
        logger.exception('Erro em job_expirar_pacotes: %s', exc)
        raise self.retry(exc=exc) from exc


# ═══════════════════════════════════════
#  SISTEMA — Limpeza de status
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_limpeza_status_atendimentos(self):
    """Atendimentos vencidos ha 24h: PENDENTE -> CANCELADO (nunca aprovado pela
    clinica, nao e no-show do cliente); AGENDADO/CONFIRMADO -> FALTOU (cliente
    nao compareceu). Usa a FSM do model (valida transicao + publica eventos:
    fila de espera no cancelamento, contador de faltas no FALTOU)."""
    try:
        limite = timezone.now() - timedelta(hours=24)

        pendentes = Atendimento.objects.filter(
            data_hora_fim__lt=limite,
            status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
        )

        for atendimento in pendentes:
            try:
                if atendimento.status == 'PENDENTE':
                    atendimento.cancelar(motivo='expirado sem aprovacao')
                    acao = 'CANCELADO (expirado)'
                else:
                    atendimento.marcar_falta()
                    acao = 'FALTOU'
                logger.info(f"[LIMPEZA] Atendimento {atendimento.pk} -> {acao} automaticamente")
            except Atendimento.TransicaoInvalida as exc:
                logger.warning(f"[LIMPEZA] Atendimento {atendimento.pk}: transicao invalida ({exc})")
                continue
    except Exception as exc:
        logger.exception('Erro em job_limpeza_status_atendimentos: %s', exc)
        raise self.retry(exc=exc) from exc


# ═══════════════════════════════════════
#  EMAIL ASYNC — wrapper fire-and-forget
# ═══════════════════════════════════════

@shared_task(
    bind=True, max_retries=3, default_retry_delay=30,
    autoretry_for=(Exception,), retry_backoff=True,
)
def send_email_async(self, funcao_nome: str, *args, **kwargs):
    """Dispara email via funcao enviar_*_email em background.

    funcao_nome: string com nome da funcao em utils.email (ex: 'enviar_confirmacao_agendamento_email').
    args/kwargs: repassados diretamente.
    """
    from .utils import email as email_mod

    func = getattr(email_mod, funcao_nome, None)
    if not func:
        logger.error('email_async_funcao_nao_encontrada', extra={'funcao': funcao_nome})
        return False
    ok = func(*args, **kwargs)
    if not ok:
        raise RuntimeError(f'Falha ao enviar email via {funcao_nome}')
    return True


# ═══════════════════════════════════════
#  LGPD — Anonimizacao automatica retencao
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def job_lgpd_purgar_inativos(self):
    """Anonimiza clientes inativos ha mais de N dias (default 5 anos)."""
    from .services.lgpd import LgpdService
    try:
        count = LgpdService.purgar_inativos()
        logger.info('lgpd_clientes_anonimizados', extra={'count': count})
        return count
    except Exception as exc:
        logger.exception('Erro em job_lgpd_purgar_inativos: %s', exc)
        raise self.retry(exc=exc) from exc


# dispatch_webhook removido junto com o workflow engine (unico caller).
