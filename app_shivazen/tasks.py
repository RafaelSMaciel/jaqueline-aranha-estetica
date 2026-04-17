"""
Celery Tasks — Plataforma de Clinicas

Jobs agendados:
  WhatsApp:  Lembrete D-1, Pesquisa NPS, Alerta detrator NPS
  Email:     Pacotes expirando, Aniversario
  Sistema:   Expirar pacotes, Limpeza status, Fila de espera
"""
import os
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from .models import Atendimento, ListaEspera, AvaliacaoNPS
import logging

logger = logging.getLogger(__name__)

CLINIC_NAME = os.environ.get('CLINIC_NAME', 'Shiva Zen')


# ═══════════════════════════════════════
#  WHATSAPP — Lembrete D-1
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_enviar_lembrete_dia_seguinte(self):
    """Envia lembrete via WhatsApp para agendamentos de amanha."""
    try:
        from .utils.whatsapp import enviar_lembrete_agendamento
        from .models import Notificacao

        amanha = timezone.now().date() + timedelta(days=1)
        agendamentos = Atendimento.objects.filter(
            data_hora_inicio__date=amanha,
            status='AGENDADO'
        ).select_related('cliente', 'profissional', 'procedimento')

        logger.info(f"[JOB LEMBRETE] {agendamentos.count()} agendamentos para amanha ({amanha}).")

        enviados = 0
        for agendamento in agendamentos:
            ja_enviou = Notificacao.objects.filter(
                atendimento=agendamento,
                tipo='LEMBRETE',
                status_envio='ENVIADO'
            ).exists()
            if ja_enviou:
                continue
            notif = enviar_lembrete_agendamento(agendamento)
            if notif and notif.status_envio == 'ENVIADO':
                enviados += 1

        logger.info(f"[JOB LEMBRETE] {enviados} lembretes enviados com sucesso.")
        return f'{enviados} lembretes enviados'
    except Exception as exc:
        logger.exception('Erro em job_enviar_lembrete_dia_seguinte: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  WHATSAPP — NPS (24h apos atendimento)
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_pesquisa_satisfacao_24h(self):
    """Envia pesquisa NPS por EMAIL 24h apos atendimento REALIZADO."""
    try:
        from .utils.email import enviar_nps_email
        from .utils.whatsapp import SITE_URL
        from .models import Notificacao
        import uuid

        site_url = SITE_URL.rstrip('/')

        limite = timezone.now() - timedelta(days=1)
        agendamentos = Atendimento.objects.filter(
            status='REALIZADO',
            data_hora_fim__lte=limite,
            avaliacaonps__isnull=True
        ).exclude(
            notificacao__tipo='NPS'
        ).select_related('cliente', 'procedimento')

        logger.info(f"[JOB NPS] {agendamentos.count()} atendimentos sem avaliacao.")

        for agendamento in agendamentos:
            token = uuid.uuid4().hex[:32]
            Notificacao.objects.create(
                atendimento=agendamento,
                tipo='NPS',
                canal='EMAIL',
                token=token,
                status_envio='PENDENTE',
            )
            nps_url = f"{site_url}/nps/{token}/"

            if agendamento.cliente.email:
                enviar_nps_email(agendamento.cliente.email, {
                    'nome': agendamento.cliente.nome_completo,
                    'procedimento': agendamento.procedimento.nome,
                    'link_nps': nps_url,
                })
                logger.info(f"[NPS] Email enviado para cliente {agendamento.cliente.pk}")
            else:
                logger.warning(f"[NPS] Cliente {agendamento.cliente.pk} sem email — NPS nao enviado")
    except Exception as exc:
        logger.exception('Erro em job_pesquisa_satisfacao_24h: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  WHATSAPP — Alerta detrator NPS
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_alerta_detrator_nps(self):
    """Alerta admin quando NPS <= 6 (detrator na escala 0-10)."""
    try:
        from .utils.whatsapp import enviar_whatsapp
        from .models import ConfiguracaoSistema

        detratores = AvaliacaoNPS.objects.filter(
            nota__lte=6,
            alerta_enviado=False
        ).select_related('atendimento__cliente', 'atendimento__procedimento')

        if not detratores.exists():
            return

        config = ConfiguracaoSistema.objects.filter(chave='whatsapp_admin').first()
        telefone_admin = config.valor if config else None

        for avaliacao in detratores:
            at = avaliacao.atendimento
            logger.warning(f"[NPS DETRATOR] Cliente {at.cliente.nome_completo} deu nota {avaliacao.nota}")

            if telefone_admin:
                mensagem = (
                    f"[{CLINIC_NAME} — ALERTA NPS]\n\n"
                    f"Cliente: {at.cliente.nome_completo}\n"
                    f"Procedimento: {at.procedimento.nome}\n"
                    f"Nota: {avaliacao.nota}/10\n"
                    f"Comentario: {avaliacao.comentario or 'Sem comentario'}"
                )
                enviar_whatsapp(telefone_admin, mensagem)

            avaliacao.alerta_enviado = True
            avaliacao.save()
    except Exception as exc:
        logger.exception('Erro em job_alerta_detrator_nps: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  EMAIL — Fila de espera
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_notificar_fila_espera(self, procedimento_id, data_livre_str):
    """Notifica interessados da fila de espera por EMAIL (fallback WhatsApp)."""
    try:
        from .utils.email import enviar_fila_espera_email
        from .utils.whatsapp import enviar_whatsapp, SITE_URL
        site_url = SITE_URL.rstrip('/')

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
            else:
                # Fallback WhatsApp se nao tem email
                mensagem = (
                    f"Ola {espera.cliente.nome_completo}! "
                    f"Uma vaga para {espera.procedimento.nome} ficou disponivel "
                    f"no dia {data_livre.strftime('%d/%m/%Y')}. "
                    f"Acesse {site_url}/agendamento para reservar! "
                    f"{CLINIC_NAME}"
                )
                enviar_whatsapp(espera.cliente.telefone, mensagem)

            espera.notificado = True
            espera.save()
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
        from .utils.whatsapp import enviar_whatsapp, SITE_URL
        from .models import PacoteCliente
        site_url = SITE_URL.rstrip('/')

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

                if sessoes_restantes > 0:
                    dados = {
                        'nome': pc.cliente.nome_completo,
                        'pacote': pc.pacote.nome,
                        'dias': dias,
                        'sessoes_restantes': sessoes_restantes,
                    }
                    if pc.cliente.email:
                        enviar_pacote_expirando_email(pc.cliente.email, dados)
                    else:
                        # Fallback WhatsApp
                        mensagem = (
                            f"Ola {pc.cliente.nome_completo}! "
                            f"Seu pacote {pc.pacote.nome} expira em {dias} dia(s). "
                            f"Voce ainda tem {sessoes_restantes} sessao(oes). "
                            f"Agende: {site_url}/agendamento "
                            f"{CLINIC_NAME}"
                        )
                        enviar_whatsapp(pc.cliente.telefone, mensagem)
                    logger.info(f"[PACOTE EXPIRANDO] Cliente {pc.cliente.pk} — {dias} dias restantes")
    except Exception as exc:
        logger.exception('Erro em job_verificar_pacotes_expirando: %s', exc)
        raise self.retry(exc=exc)


# ═══════════════════════════════════════
#  EMAIL — Aniversario
# ═══════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def job_aniversario_clientes(self):
    """Envia email de aniversario com desconto para clientes aniversariantes."""
    try:
        from .utils.email import enviar_aniversario_email
        from .models import Cliente

        hoje = timezone.now().date()
        aniversariantes = Cliente.objects.filter(
            data_nascimento__month=hoje.month,
            data_nascimento__day=hoje.day,
            ativo=True,
            email__isnull=False,
        ).exclude(email='')

        logger.info(f"[JOB ANIVERSARIO] {aniversariantes.count()} aniversariante(s) hoje.")

        for cliente in aniversariantes:
            enviar_aniversario_email(cliente.email, {
                'nome': cliente.nome_completo,
                'desconto': 15,  # Configuravel futuramente
            })
    except Exception as exc:
        logger.exception('Erro em job_aniversario_clientes: %s', exc)
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
