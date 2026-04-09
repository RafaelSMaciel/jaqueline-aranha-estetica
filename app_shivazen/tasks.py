from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from .models import Atendimento, ListaEspera, AvaliacaoNPS
import logging

logger = logging.getLogger(__name__)


@shared_task
def job_enviar_lembrete_dia_seguinte():
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


@shared_task
def job_enviar_lembrete_2h():
    from .utils.whatsapp import enviar_whatsapp
    from .models import Notificacao

    agora = timezone.now()
    limite = agora + timedelta(hours=2)
    agendamentos = Atendimento.objects.filter(
        data_hora_inicio__range=[agora, limite],
        status='AGENDADO'
    ).select_related('cliente', 'profissional', 'procedimento')

    for agendamento in agendamentos:
        notif_original = Notificacao.objects.filter(
            atendimento=agendamento,
            tipo='LEMBRETE',
            status_envio='ENVIADO'
        ).first()
        if not notif_original or notif_original.resposta_cliente:
            continue
        hora = agendamento.data_hora_inicio.strftime('%H:%M')
        mensagem = (
            f"Ola {agendamento.cliente.nome_completo}! "
            f"Seu agendamento na Shiva Zen e daqui a 2 horas ({hora}). "
            f"Voce ainda nao confirmou. Por favor confirme pelo link enviado anteriormente. "
            f"Shiva Zen"
        )
        enviar_whatsapp(agendamento.cliente.telefone, mensagem)

    logger.info(f"[JOB 2H] {agendamentos.count()} lembretes de 2h processados.")


@shared_task
def job_notificar_fila_espera(procedimento_id, data_livre_str):
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
        mensagem = (
            f"Ola {espera.cliente.nome_completo}! "
            f"Uma vaga para {espera.procedimento.nome} ficou disponivel "
            f"no dia {data_livre.strftime('%d/%m/%Y')}. "
            f"Acesse {site_url}/agendamento para reservar! "
            f"Shiva Zen"
        )
        enviar_whatsapp(espera.cliente.telefone, mensagem)
        espera.notificado = True
        espera.save()


@shared_task
def job_pesquisa_satisfacao_24h():
    from .utils.whatsapp import enviar_whatsapp, SITE_URL
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
            canal='WHATSAPP',
            token=token,
            status_envio='PENDENTE',
        )
        nps_url = f"{site_url}/nps/{token}/"
        mensagem = (
            f"Ola {agendamento.cliente.nome_completo}! "
            f"Como foi seu atendimento de {agendamento.procedimento.nome}? "
            f"Avalie pelo link: {nps_url} "
            f"Sua opiniao e muito importante para nos! "
            f"Shiva Zen"
        )
        enviar_whatsapp(agendamento.cliente.telefone, mensagem)


@shared_task
def job_alerta_detrator_nps():
    """Alerta admin quando NPS <= 6 (detrator na escala 0-10)"""
    detratores = AvaliacaoNPS.objects.filter(
        nota__lte=6,
        alerta_enviado=False
    ).select_related('atendimento__cliente', 'atendimento__procedimento')

    for avaliacao in detratores:
        at = avaliacao.atendimento
        logger.warning(f"[NPS DETRATOR] Cliente {at.cliente.nome_completo} deu nota {avaliacao.nota}")
        avaliacao.alerta_enviado = True
        avaliacao.save()


@shared_task
def job_verificar_pacotes_expirando():
    """Notifica clientes com pacotes expirando em 7 dias ou 1 dia"""
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
                mensagem = (
                    f"Ola {pc.cliente.nome_completo}! "
                    f"Seu pacote {pc.pacote.nome} expira em {dias} dia(s). "
                    f"Voce ainda tem {sessoes_restantes} sessao(oes) disponiveis. "
                    f"Agende agora em {site_url}/agendamento! "
                    f"Shiva Zen"
                )
                enviar_whatsapp(pc.cliente.telefone, mensagem)
                logger.info(f"[PACOTE EXPIRANDO] Cliente {pc.cliente.pk} — {dias} dias restantes")


@shared_task
def job_expirar_pacotes():
    """Expira pacotes vencidos automaticamente"""
    from .models import PacoteCliente

    hoje = timezone.now().date()
    expirados = PacoteCliente.objects.filter(
        status='ATIVO',
        data_expiracao__lt=hoje
    ).update(status='EXPIRADO')

    if expirados:
        logger.info(f"[PACOTE] {expirados} pacote(s) expirado(s) automaticamente.")


@shared_task
def job_limpeza_status_atendimentos():
    """Marca como FALTOU atendimentos passados há 24h ainda em AGENDADO/CONFIRMADO"""
    limite = timezone.now() - timedelta(hours=24)

    pendentes = Atendimento.objects.filter(
        data_hora_fim__lt=limite,
        status__in=['AGENDADO', 'CONFIRMADO']
    )

    for atendimento in pendentes:
        atendimento.status = 'FALTOU'
        atendimento.save()
        logger.info(f"[LIMPEZA] Atendimento {atendimento.pk} marcado como FALTOU automaticamente")
