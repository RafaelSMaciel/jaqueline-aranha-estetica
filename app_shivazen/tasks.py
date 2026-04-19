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

CLINIC_NAME = os.environ.get('CLINIC_NAME', 'Shiva Zen')


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
                status_envio='ENVIADO'
            ).exists()
            if ja_enviou:
                continue
            notif = enviar_confirmacao_d1(agendamento)
            if notif and notif.status_envio == 'ENVIADO':
                enviados += 1

        logger.info(f"[JOB LEMBRETE] {enviados} lembretes enviados com sucesso.")
        return f'{enviados} lembretes enviados'
    except Exception as exc:
        logger.exception('Erro em job_enviar_lembrete_dia_seguinte: %s', exc)
        raise self.retry(exc=exc)


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
        agendamentos = Atendimento.objects.filter(
            status='REALIZADO',
            data_hora_fim__lte=limite,
            avaliacaonps__isnull=True,
            cliente__consent_whatsapp_nps=True,
        ).exclude(
            notificacao__tipo='NPS'
        ).select_related('cliente', 'procedimento')

        logger.info(f"[JOB NPS] {agendamentos.count()} atendimentos sem avaliacao e com consent.")

        enviados = 0
        for agendamento in agendamentos:
            if not agendamento.cliente.telefone:
                logger.warning(f"[NPS WA] Cliente {agendamento.cliente.pk} sem telefone — NPS nao enviado")
                continue

            token = secrets.token_urlsafe(32)
            Notificacao.objects.create(
                atendimento=agendamento,
                tipo='NPS',
                canal='WHATSAPP',
                token=token,
                status_envio='PENDENTE',
            )
            nps_url = f"{site_url}/nps/{token}/"

            if enviar_nps_whatsapp(agendamento, nps_url, token):
                enviados += 1

        logger.info(f"[JOB NPS] {enviados} NPS enviados via WhatsApp.")
        return f'{enviados} NPS enviados'
    except Exception as exc:
        logger.exception('Erro em job_pesquisa_satisfacao_24h: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  EMAIL — Alerta detrator NPS (admin)
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_alerta_detrator_nps(self):
    """Alerta admin por EMAIL quando NPS <= 6 (detrator)."""
    try:
        from django.core.mail import send_mail
        from .models import ConfiguracaoSistema

        detratores = AvaliacaoNPS.objects.filter(
            nota__lte=6,
            alerta_enviado=False
        ).select_related('atendimento__cliente', 'atendimento__procedimento')

        if not detratores.exists():
            return

        config = ConfiguracaoSistema.objects.filter(chave='email_admin').first()
        email_admin = config.valor if config else os.environ.get('ADMIN_EMAIL', '')

        if not email_admin:
            logger.warning('[NPS DETRATOR] Sem email_admin configurado — alerta nao enviado')
            for avaliacao in detratores:
                logger.warning(
                    f"[NPS DETRATOR] Cliente {avaliacao.atendimento.cliente.nome_completo} "
                    f"deu nota {avaliacao.nota}"
                )
            return

        default_from = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@clinica.com.br')

        for avaliacao in detratores:
            at = avaliacao.atendimento
            assunto = f'[{CLINIC_NAME}] ALERTA NPS — {at.cliente.nome_completo} nota {avaliacao.nota}'
            corpo = (
                f'Cliente: {at.cliente.nome_completo}\n'
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
                    f"[NPS DETRATOR] Alerta enviado: {at.cliente.nome_completo} nota {avaliacao.nota}"
                )
            except Exception as e:
                logger.error('[NPS DETRATOR] Falha ao enviar alerta: %s', e)
    except Exception as exc:
        logger.exception('Erro em job_alerta_detrator_nps: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  EMAIL — Fila de espera
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_notificar_fila_espera(self, procedimento_id, data_livre_str):
    """Notifica interessados da fila de espera por EMAIL."""
    try:
        from .utils.email import enviar_fila_espera_email

        data_livre = timezone.datetime.fromisoformat(data_livre_str).date()
        logger.info(f"[JOB ESPERA] Vaga liberada para procedimento {procedimento_id} na data {data_livre}.")

        interessados = ListaEspera.objects.filter(
            procedimento_id=procedimento_id,
            data_desejada=data_livre,
            notificado=False
        ).select_related('cliente', 'procedimento').order_by('criado_em')

        for espera in interessados:
            if espera.cliente.email:
                enviar_fila_espera_email(espera.cliente.email, {
                    'nome': espera.cliente.nome_completo,
                    'procedimento': espera.procedimento.nome,
                    'data': data_livre.strftime('%d/%m/%Y'),
                })
                espera.notificado = True
                espera.save(update_fields=['notificado'])
            else:
                logger.warning(
                    '[JOB ESPERA] Cliente %s sem email — nao notificado',
                    espera.cliente.pk,
                )
    except Exception as exc:
        logger.exception('Erro em job_notificar_fila_espera: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  EMAIL — Pacotes expirando
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_verificar_pacotes_expirando(self):
    """Notifica clientes com pacotes expirando em 7 ou 1 dia — por EMAIL."""
    try:
        from .utils.email import enviar_pacote_expirando_email
        from .models import PacoteCliente

        hoje = timezone.now().date()

        for dias in [7, 1]:
            data_alvo = hoje + timedelta(days=dias)
            pacotes = PacoteCliente.objects.filter(
                status='ATIVO',
                data_expiracao=data_alvo
            ).select_related('cliente', 'pacote')

            for pc in pacotes:
                sessoes_restantes = 0
                for item in pc.pacote.itens.all():
                    feitas = pc.sessoes_realizadas.filter(
                        atendimento__procedimento=item.procedimento
                    ).count()
                    sessoes_restantes += max(0, item.quantidade_sessoes - feitas)

                if sessoes_restantes > 0 and pc.cliente.email:
                    enviar_pacote_expirando_email(pc.cliente.email, {
                        'nome': pc.cliente.nome_completo,
                        'pacote': pc.pacote.nome,
                        'dias': dias,
                        'sessoes_restantes': sessoes_restantes,
                    })
                    logger.info(f"[PACOTE EXPIRANDO] Cliente {pc.cliente.pk} — {dias} dias restantes")
                elif sessoes_restantes > 0:
                    logger.warning(
                        '[PACOTE EXPIRANDO] Cliente %s sem email — nao notificado',
                        pc.cliente.pk,
                    )
    except Exception as exc:
        logger.exception('Erro em job_verificar_pacotes_expirando: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  EMAIL — Aniversario
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_aniversario_clientes(self):
    """Envia email de aniversario com desconto para clientes aniversariantes.

    Respeita consent_email_marketing=True.
    """
    try:
        from .utils.email import enviar_aniversario_email
        from .models import Cliente

        hoje = timezone.now().date()
        aniversariantes = Cliente.objects.filter(
            data_nascimento__month=hoje.month,
            data_nascimento__day=hoje.day,
            ativo=True,
            email__isnull=False,
            consent_email_marketing=True,
        ).exclude(email='')

        logger.info(f"[JOB ANIVERSARIO] {aniversariantes.count()} aniversariante(s) hoje com consent.")

        for cliente in aniversariantes:
            enviar_aniversario_email(cliente.email, {
                'nome': cliente.nome_completo,
                'desconto': 15,
            })
    except Exception as exc:
        logger.exception('Erro em job_aniversario_clientes: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  EMAIL — Promocao mensal (opt-in)
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_promocao_mensal(self, assunto, corpo_html_partial, cupom=None, validade_dias=30):
    """Envia email promocional para clientes com consent_email_marketing=True.

    Parametros:
      assunto: subject do email
      corpo_html_partial: snippet HTML inserido no template base de promocao
      cupom: codigo de cupom (opcional)
      validade_dias: dias ate expiracao do cupom (default 30)
    """
    try:
        from .utils.email import _enviar_email
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
            ok = _enviar_email(
                destinatario=cliente.email,
                assunto=assunto,
                template='email/promocao.html',
                contexto={
                    'nome': cliente.nome_completo,
                    'corpo_html': corpo_html_partial,
                    'cupom': cupom,
                    'validade': validade,
                    'unsubscribe_token': cliente.unsubscribe_token,
                },
            )
            if ok:
                enviados += 1
        logger.info(f"[JOB PROMOCAO] {enviados} emails enviados.")
        return f'{enviados} promocoes enviadas'
    except Exception as exc:
        logger.exception('Erro em job_promocao_mensal: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  SISTEMA — Expirar pacotes
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_expirar_pacotes(self):
    """Expira pacotes vencidos automaticamente."""
    try:
        from .models import PacoteCliente

        hoje = timezone.now().date()
        expirados = PacoteCliente.objects.filter(
            status='ATIVO',
            data_expiracao__lt=hoje
        ).update(status='EXPIRADO')

        if expirados:
            logger.info(f"[PACOTE] {expirados} pacote(s) expirado(s) automaticamente.")
    except Exception as exc:
        logger.exception('Erro em job_expirar_pacotes: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  SISTEMA — Limpeza de status
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_limpeza_status_atendimentos(self):
    """Marca como FALTOU atendimentos passados ha 24h ainda em PENDENTE/AGENDADO/CONFIRMADO."""
    try:
        limite = timezone.now() - timedelta(hours=24)

        pendentes = Atendimento.objects.filter(
            data_hora_fim__lt=limite,
            status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
        )

        for atendimento in pendentes:
            atendimento.status = 'FALTOU'
            atendimento.save()
            logger.info(f"[LIMPEZA] Atendimento {atendimento.pk} marcado como FALTOU automaticamente")
    except Exception as exc:
        logger.exception('Erro em job_limpeza_status_atendimentos: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  EMAIL ASYNC — wrapper fire-and-forget
# ═══════════════════════════════════════

@shared_task(
    bind=True, max_retries=3, default_retry_delay=30,
    autoretry_for=(Exception,), retry_backoff=True,
)
def send_email_async(self, funcao_nome, *args, **kwargs):
    """Dispara email via funcao enviar_*_email em background.

    funcao_nome: string com nome da funcao em utils.email (ex: 'enviar_codigo_otp_email').
    args/kwargs: repassados diretamente.
    """
    from . import utils
    from .utils import email as email_mod

    func = getattr(email_mod, funcao_nome, None)
    if not func:
        logger.error('[EMAIL ASYNC] funcao %s nao encontrada', funcao_nome)
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
        logger.info('[LGPD] %s clientes inativos anonimizados', count)
        return count
    except Exception as exc:
        logger.exception('Erro em job_lgpd_purgar_inativos: %s', exc)
        raise self.retry(exc=exc)
