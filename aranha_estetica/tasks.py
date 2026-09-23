"""
Celery Tasks — Plataforma de Clinicas

Estrategia de canais (aprovada 2026-04-18):
  WhatsApp:  Confirmacao D-1, NPS pos-atendimento, aviso de vaga (lista de espera)
  Email:     confirmacao, cancelamento, fila, pacotes, aniversario,
             promocoes, termos, alertas admin (detrator NPS)
  SMS:       OTP — integrado via utils/sms.py

Regras transversais:
  - "hoje/amanha" sempre no fuso da clinica (utils/datas), nunca data UTC;
  - canal sem credencial fora de DEBUG => nada e marcado como enviado;
  - consentimento do cliente respeitado por canal/finalidade;
  - prod roda Celery eager (sem worker): retry sincrono reexecutaria o lote
    no mesmo request, entao em eager o erro propaga (cron ve 500) sem retry.
Jobs de manutencao (housekeeping, feriados) ficam em tasks_manutencao.py.
"""
import logging
import os
import secrets
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Atendimento, AvaliacaoNPS
from .utils.datas import fmt_local, hoje

logger = logging.getLogger(__name__)

# Janela do NPS: so atendimentos recentes (evita disparo p/ todo o historico
# na primeira execucao) e no maximo N tentativas por atendimento.
NPS_JANELA_DIAS = 7
NPS_MAX_FALHAS = 3


def _retry_ou_propaga(task, exc):
    """Retry com espera so em worker real; em eager propaga o erro."""
    if getattr(task.request, 'is_eager', False):
        raise exc
    raise task.retry(exc=exc) from exc


# Workflow engine removido na remodelagem v2.1 fase 1d — regras configuraveis
# por UI eram over-engineering p/ 1 clinica; reacoes vivem em services/handlers.


# ═══════════════════════════════════════
#  WHATSAPP — Confirmacao D-1
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_enviar_lembrete_dia_seguinte(self):
    """Envia confirmacao D-1 via WhatsApp (template) para agendamentos de amanha.

    Respeita o consentimento do checkbox 'Aceito receber lembrete ... por
    WhatsApp' (consent_whatsapp_confirmacao).
    """
    try:
        from .utils.whatsapp import enviar_confirmacao_d1, pode_enviar_whatsapp
        from .models import Notificacao

        if not pode_enviar_whatsapp():
            logger.warning('lembrete_d1_whatsapp_nao_configurado')
            return 'whatsapp nao configurado'

        amanha = hoje() + timedelta(days=1)
        agendamentos = Atendimento.objects.filter(
            data_hora_inicio__date=amanha,
            status='AGENDADO',
            cliente__consent_whatsapp_confirmacao=True,
        ).select_related('cliente', 'profissional', 'procedimento')

        logger.info(f"[JOB LEMBRETE] {agendamentos.count()} agendamentos para amanha ({amanha}).")

        enviados = 0
        for agendamento in agendamentos:
            if not agendamento.cliente.telefone:
                continue
            ja_enviou = Notificacao.objects.filter(
                atendimento=agendamento,
                tipo='LEMBRETE',
                canal='WHATSAPP',
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
                    agendamento.pk, type(e).__name__, exc_info=True,
                )

        logger.info(f"[JOB LEMBRETE] {enviados} lembretes enviados com sucesso.")
        return f'{enviados} lembretes enviados'
    except Exception as exc:
        logger.exception('Erro em job_enviar_lembrete_dia_seguinte: %s', exc)
        _retry_ou_propaga(self, exc)


# ═══════════════════════════════════════
#  WHATSAPP — NPS 24h pos atendimento
# ═══════════════════════════════════════

def candidatos_nps(agora=None):
    """Atendimentos REALIZADO elegiveis ao NPS.

    - terminaram ha 24h+ e ha no maximo NPS_JANELA_DIAS;
    - sem avaliacao; cliente com consent_whatsapp_nps e sem opt-out geral;
    - sem NPS PENDENTE/ENVIADO (Exists correlacionado — um exclude() com
      duas condicoes na FK reversa virava dois EXISTS independentes e
      bloqueava o retry de quem tinha NPS FALHOU + LEMBRETE ENVIADO);
    - menos de NPS_MAX_FALHAS tentativas falhas.
    """
    from django.db.models import Count, Exists, OuterRef, Q
    from .models import Notificacao

    agora = agora or timezone.now()
    nps_ativo = Notificacao.objects.filter(
        atendimento=OuterRef('pk'), tipo='NPS', status__in=['PENDENTE', 'ENVIADO'],
    )
    return (
        Atendimento.objects.filter(
            status='REALIZADO',
            data_hora_fim__lte=agora - timedelta(days=1),
            data_hora_fim__gte=agora - timedelta(days=NPS_JANELA_DIAS),
            avaliacaonps__isnull=True,
            cliente__consent_whatsapp_nps=True,
            cliente__aceita_comunicacao=True,
        )
        .exclude(Exists(nps_ativo))
        .annotate(nps_falhas=Count(
            'notificacao', filter=Q(notificacao__tipo='NPS', notificacao__status='FALHOU'),
        ))
        .filter(nps_falhas__lt=NPS_MAX_FALHAS)
        .select_related('cliente', 'procedimento')
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_pesquisa_satisfacao_24h(self):
    """Envia NPS por WhatsApp 24h apos atendimento REALIZADO.

    Requer consent_whatsapp_nps=True do cliente. Sem consent, ignora.
    """
    try:
        from .utils.whatsapp import enviar_nps_whatsapp, pode_enviar_whatsapp
        from .models import Notificacao

        # Sem canal: nao cria Notificacao (nem FALHOU) — quando o WhatsApp
        # for configurado, a janela de 7 dias ainda pega os recentes.
        if not pode_enviar_whatsapp():
            logger.warning('nps_whatsapp_nao_configurado')
            return 'whatsapp nao configurado'

        site_url = settings.SITE_URL.rstrip('/')
        agendamentos = candidatos_nps()

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

            # enviar_nps_whatsapp grava ENVIADO/FALHOU na Notificacao (nunca
            # fica PENDENTE orfa); FALHOU e re-tentavel ate NPS_MAX_FALHAS.
            try:
                if enviar_nps_whatsapp(agendamento, nps_url, token):
                    enviados += 1
            except Exception as e:
                Notificacao.objects.filter(pk=notif.pk).update(status='FALHOU')
                logger.error('[JOB NPS] Falha no atendimento %s: %s', agendamento.pk, type(e).__name__)

        logger.info(f"[JOB NPS] {enviados} NPS enviados via WhatsApp.")
        return f'{enviados} NPS enviados'
    except Exception as exc:
        logger.exception('Erro em job_pesquisa_satisfacao_24h: %s', exc)
        _retry_ou_propaga(self, exc)


# ═══════════════════════════════════════
#  EMAIL — Alerta detrator NPS (admin)
# ═══════════════════════════════════════

def email_alerta_detrator() -> str:
    """Destino do alerta: Configuracao email_admin (qualquer caixa) > env
    ADMIN_EMAIL > e-mail de contato da clinica (Branding)."""
    from .models import Configuracao
    from .utils.branding import get_branding

    cfg = Configuracao.objects.filter(chave__iexact='email_admin').first()
    valor = (cfg.valor or '').strip() if cfg else ''
    return valor or os.environ.get('ADMIN_EMAIL', '').strip() or get_branding().get('CLINIC_EMAIL', '')


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_alerta_detrator_nps(self):
    """Alerta admin por EMAIL quando NPS <= 6 (detrator)."""
    try:
        from django.core.mail import send_mail
        from .utils.branding import get_branding
        from .utils.email import email_configurado

        detratores = list(AvaliacaoNPS.objects.filter(
            nota__lte=6,
            alerta_enviado=False
        ).select_related('atendimento__cliente', 'atendimento__procedimento', 'atendimento__profissional'))

        if not detratores:
            return

        email_admin = email_alerta_detrator()
        if not email_admin:
            logger.warning('nps_detrator_sem_email_admin', extra={'pendentes': len(detratores)})
            return
        # Sem backend real (console/dummy fora de DEBUG) nao marca alerta_enviado:
        # o alerta sai quando o e-mail for configurado.
        if not email_configurado():
            logger.warning('nps_detrator_email_nao_configurado', extra={'pendentes': len(detratores)})
            return

        clinica = get_branding()['CLINIC_NAME']
        default_from = getattr(settings, 'DEFAULT_FROM_EMAIL', '') or 'noreply@localhost'

        for avaliacao in detratores:
            at = avaliacao.atendimento
            assunto = f'[{clinica}] Alerta NPS — {at.cliente.nome} nota {avaliacao.nota}'
            corpo = (
                f'Cliente: {at.cliente.nome}\n'
                f'Procedimento: {at.procedimento.nome}\n'
                f'Profissional: {at.profissional.nome if at.profissional_id else "-"}\n'
                f'Data do atendimento: {fmt_local(at.data_hora_inicio)}\n'
                f'Nota: {avaliacao.nota}/10\n'
                f'Comentário: {avaliacao.comentario or "(sem comentário)"}\n'
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
                    extra={'cliente_id': at.cliente_id, 'nota': avaliacao.nota},
                )
            except Exception as e:
                # Nao marca alerta_enviado -> re-tentado no proximo run (idempotente).
                logger.error('nps_detrator_falha_alerta', extra={'erro': type(e).__name__})
    except Exception as exc:
        logger.exception('Erro em job_alerta_detrator_nps: %s', exc)
        _retry_ou_propaga(self, exc)


# Lista de espera: aviso de vaga saiu daqui — um unico mecanismo em
# services/lista_espera_service (disparado por signals, e-mail no on_commit).


# ═══════════════════════════════════════
#  EMAIL — Pacotes expirando
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_verificar_pacotes_expirando(self):
    """Notifica clientes com pacotes expirando em 7 ou 1 dia — por EMAIL."""
    try:
        from .utils.email import enviar_pacote_expirando_email
        from .models import CompraPacote

        data_hoje = hoje()
        enviados = 0

        for dias in [7, 1]:
            data_alvo = data_hoje + timedelta(days=dias)
            pacotes = CompraPacote.objects.filter(
                status='ATIVO',
                data_expiracao=data_alvo
            ).select_related('cliente', 'pacote').prefetch_related('pacote__itens__procedimento')

            for pc in pacotes:
                # Mesma fonte de saldo da ficha do cliente (1 query agregada).
                sessoes_restantes = pc.sessoes_restantes()

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
                    ok = enviar_pacote_expirando_email(pc.cliente.email, {
                        'nome': pc.cliente.nome,
                        'pacote': pc.pacote.nome,
                        'dias': dias,
                        'sessoes_restantes': sessoes_restantes,
                        # Sessao conta pela data em que acontece (signals).
                        'valido_ate': pc.data_expiracao.strftime('%d/%m/%Y'),
                    })
                    if ok:
                        enviados += 1
                        logger.info(f"[PACOTE EXPIRANDO] Cliente {pc.cliente.pk} — {dias} dias restantes")
                    else:
                        logger.warning('[PACOTE EXPIRANDO] E-mail nao enviado ao cliente %s', pc.cliente.pk)
                except Exception as e:
                    logger.error(
                        '[PACOTE EXPIRANDO] Falha ao notificar cliente %s: %s',
                        pc.cliente.pk, type(e).__name__, exc_info=True,
                    )
        return f'{enviados} avisos enviados'
    except Exception as exc:
        logger.exception('Erro em job_verificar_pacotes_expirando: %s', exc)
        _retry_ou_propaga(self, exc)


# ═══════════════════════════════════════
#  EMAIL — Aniversario
# ═══════════════════════════════════════

def aniversariantes_do_dia(data=None):
    """Clientes ativos que fazem aniversario na data (29/02 comemora em 28/02
    nos anos nao bissextos)."""
    import calendar
    from django.db.models import Q
    from .models import Cliente

    data = data or hoje()
    filtro = Q(data_nascimento__month=data.month, data_nascimento__day=data.day)
    if data.month == 2 and data.day == 28 and not calendar.isleap(data.year):
        filtro |= Q(data_nascimento__month=2, data_nascimento__day=29)
    return Cliente.objects.filter(filtro, ativo=True)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_aniversario_clientes(self):
    """Envia e-mail de felicitacao de aniversario (marketing, sem desconto:
    nao ha mecanismo que aplique cupom/percentual no agendamento).

    Requer consent_email_marketing=True e sem opt-out geral (aceita_comunicacao).
    Sai com link + header List-Unsubscribe (LGPD / RFC 8058).
    WhatsApp de aniversario NAO e enviado: o unico consentimento de WhatsApp
    coletado e o de lembrete do agendamento, que nao cobre marketing.
    """
    try:
        from .utils.email import enviar_aniversario_email

        aniversariantes = aniversariantes_do_dia()
        emails_enviados = 0

        for cliente in aniversariantes:
            if not (cliente.email and cliente.consent_email_marketing and cliente.aceita_comunicacao):
                continue
            dados = {'nome': cliente.nome}
            # Falha pontual nao re-dispara o batch (retry reenviaria aos ja
            # parabenizados) — loga e segue.
            try:
                if enviar_aniversario_email(cliente.email, dados, unsub_token=cliente.token_descadastro):
                    emails_enviados += 1
            except Exception as e:
                logger.error(
                    'aniversario_email_falha',
                    extra={'cliente_id': cliente.pk, 'erro': type(e).__name__},
                )

        logger.info(
            'aniversario_disparado',
            extra={'aniversariantes': aniversariantes.count(), 'emails': emails_enviados},
        )
        return f'{emails_enviados} e-mails de aniversario'
    except Exception as exc:
        logger.exception('aniversario_erro')
        _retry_ou_propaga(self, exc)


# ═══════════════════════════════════════
#  EMAIL — Promocao mensal (opt-in)
# ═══════════════════════════════════════

def validade_promocao(validade_dias: int = 30, data_fim=None) -> str:
    """'dd/mm/aaaa' anunciado no e-mail: hoje (local) + validade_dias, mas
    nunca depois do fim da promocao (data_fim: date ou ISO 'aaaa-mm-dd')."""
    from datetime import date

    limite = hoje() + timedelta(days=validade_dias)
    if data_fim:
        if not isinstance(data_fim, date):
            data_fim = date.fromisoformat(str(data_fim)[:10])
        limite = min(limite, data_fim)
    return limite.strftime('%d/%m/%Y')


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_promocao_mensal(self, assunto: str, corpo_html_partial: str, cupom: str = None,
                        validade_dias: int = 30, data_fim=None):
    """Envia email promocional para clientes com consent_email_marketing=True.

    Parametros:
      assunto: subject do email
      corpo_html_partial: snippet HTML inserido no template base de promocao
      cupom: IGNORADO — nenhum fluxo aceita cupom (promessa sem mecanismo nao
        vai no e-mail); aceito so p/ nao quebrar chamadas antigas
      validade_dias: dias de validade anunciada (default 30)
      data_fim: fim da promocao (date ou ISO) — teto da validade anunciada
    """
    try:
        from .utils.email import email_configurado, enviar_promocao_email
        from .models import Cliente

        if not email_configurado():
            logger.warning('promocao_email_nao_configurado')
            return 'email nao configurado'

        destinatarios = Cliente.objects.filter(
            ativo=True,
            email__isnull=False,
            consent_email_marketing=True,
            aceita_comunicacao=True,
        ).exclude(email='')

        logger.info(f"[JOB PROMOCAO] {destinatarios.count()} destinatario(s) com consent.")

        validade = validade_promocao(validade_dias, data_fim)
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
                    cliente.pk, type(e).__name__, exc_info=True,
                )
        logger.info(f"[JOB PROMOCAO] {enviados} emails enviados.")
        return f'{enviados} promocoes enviadas'
    except Exception as exc:
        logger.exception('Erro em job_promocao_mensal: %s', exc)
        _retry_ou_propaga(self, exc)


# ═══════════════════════════════════════
#  SISTEMA — Expirar pacotes
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_expirar_pacotes(self):
    """Expira pacotes vencidos (data_expiracao < hoje LOCAL: o ultimo dia vale)."""
    try:
        from .models import CompraPacote

        expirados = CompraPacote.objects.filter(
            status='ATIVO',
            data_expiracao__lt=hoje()
        ).update(status='EXPIRADO')

        if expirados:
            logger.info(f"[PACOTE] {expirados} pacote(s) expirado(s) automaticamente.")
        return f'{expirados} pacotes expirados'
    except Exception as exc:
        logger.exception('Erro em job_expirar_pacotes: %s', exc)
        _retry_ou_propaga(self, exc)


# ═══════════════════════════════════════
#  SISTEMA — Limpeza de status
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_limpeza_status_atendimentos(self):
    """Atendimentos vencidos ha 24h.

    - PENDENTE -> CANCELADO: a clinica nunca aprovou (nao e falta do cliente).
    - AGENDADO/CONFIRMADO: NAO vira FALTOU automaticamente. Falta e terminal
      na FSM, conta no bloqueio de agendamento online e impede corrigir para
      REALIZADO (comissao, pacote, NPS) — so a equipe decide. O job apenas
      lista os atendimentos sem desfecho no log para a equipe resolver.
    Usa a FSM do model (valida transicao + auditoria).
    """
    try:
        limite = timezone.now() - timedelta(hours=24)

        pendentes = Atendimento.objects.filter(data_hora_fim__lt=limite, status='PENDENTE')
        cancelados = 0
        for atendimento in pendentes:
            try:
                atendimento.cancelar(motivo='expirado sem aprovacao')
                cancelados += 1
                logger.info(f"[LIMPEZA] Atendimento {atendimento.pk} -> CANCELADO (expirado)")
            except Atendimento.TransicaoInvalida as exc:
                logger.warning(f"[LIMPEZA] Atendimento {atendimento.pk}: transicao invalida ({exc})")

        sem_desfecho = list(
            Atendimento.objects.filter(
                data_hora_fim__lt=limite, status__in=['AGENDADO', 'CONFIRMADO'],
            ).values_list('pk', flat=True)[:200]
        )
        if sem_desfecho:
            logger.warning(
                'atendimentos_sem_desfecho',
                extra={'count': len(sem_desfecho), 'ids': sem_desfecho},
            )
        return f'{cancelados} pendentes cancelados; {len(sem_desfecho)} sem desfecho'
    except Exception as exc:
        logger.exception('Erro em job_limpeza_status_atendimentos: %s', exc)
        _retry_ou_propaga(self, exc)


# ═══════════════════════════════════════
#  EMAIL ASYNC — wrapper fire-and-forget
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_async(self, funcao_nome: str, *args, **kwargs):
    """Dispara email via funcao enviar_*_email em background.

    funcao_nome: string com nome da funcao em utils.email (ex: 'enviar_confirmacao_agendamento_email').
    args/kwargs: repassados diretamente.
    Em eager (prod sem worker) roda UMA vez no request e devolve False em
    falha — sem retry sincrono. Com worker real, re-tenta com espera.
    """
    from .utils import email as email_mod

    func = getattr(email_mod, funcao_nome, None)
    if not func or not funcao_nome.startswith('enviar_'):
        logger.error('email_async_funcao_nao_encontrada', extra={'funcao': funcao_nome})
        return False
    ok = func(*args, **kwargs)
    if ok:
        return True
    if getattr(self.request, 'is_eager', False) or not email_mod.email_configurado():
        return False
    if self.request.retries >= self.max_retries:
        logger.error('email_async_falhou', extra={'funcao': funcao_nome})
        return False
    raise self.retry(countdown=60 * (2 ** self.request.retries))


# ═══════════════════════════════════════
#  LGPD — Anonimizacao automatica retencao
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def job_lgpd_purgar_inativos(self):
    """Retencao LGPD:
    - anonimiza clientes sem atendimento ha 5 anos
      (LgpdService.RETENCAO_CLIENTE_INATIVO_DIAS) ou soft-deletados ha 30 dias;
      nunca quem tem prontuario, aceite, pacote ou atendimento de saude
      dentro de 20 anos (ver LgpdService.candidatos_purga);
    - apaga fichas de anamnese de pedidos nao realizados ha 90 dias
      (LgpdService.fichas_sem_atendimento_para_purga)."""
    from .services.lgpd import LgpdService
    try:
        count = LgpdService.purgar_inativos()
        logger.info('lgpd_clientes_anonimizados', extra={'count': count})
        fichas = LgpdService.purgar_fichas_sem_atendimento()
        return f'{count} clientes anonimizados; {fichas} fichas apagadas'
    except Exception as exc:
        logger.exception('Erro em job_lgpd_purgar_inativos: %s', exc)
        _retry_ou_propaga(self, exc)


# dispatch_webhook removido junto com o workflow engine (unico caller).
