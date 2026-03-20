from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from .models import Atendimento

import logging
logger = logging.getLogger(__name__)


@shared_task
def job_enviar_lembrete_dia_seguinte():
    """
    Busca atendimentos marcados para amanha com status AGENDADO
    e envia lembrete via WhatsApp com link de confirmacao/cancelamento.
    """
    from .utils.whatsapp import enviar_lembrete_agendamento
    from .models import Notificacao

    amanha = timezone.now().date() + timedelta(days=1)

    agendamentos = Atendimento.objects.filter(
        data_hora_inicio__date=amanha,
        status_atendimento='AGENDADO'
    ).select_related('cliente', 'profissional', 'procedimento')

    logger.info(f"[JOB LEMBRETE] {agendamentos.count()} agendamentos para amanha ({amanha}).")

    enviados = 0
    for agendamento in agendamentos:
        # Nao enviar se ja tem lembrete para este atendimento
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
    """
    Lembrete adicional 2h antes do agendamento para quem ainda nao confirmou.
    """
    from .utils.whatsapp import enviar_whatsapp
    from .models import Notificacao

    agora = timezone.now()
    limite = agora + timedelta(hours=2)

    agendamentos = Atendimento.objects.filter(
        data_hora_inicio__range=[agora, limite],
        status_atendimento='AGENDADO'  # Ainda nao confirmou
    ).select_related('cliente', 'profissional', 'procedimento')

    for agendamento in agendamentos:
        # Buscar o lembrete original para pegar o token
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

    logger.info(f"[JOB 2H] {agendamentos.count()} lembretes de 2h enviados.")


from .models import Atendimento, ListaEspera, AvaliacaoNPS

@shared_task
def job_notificar_fila_espera(procedimento_id, data_livre_str):
    """
    Acionado quando alguem cancela um agendamento. Avisa quem esta na fila.
    """
    from .utils.whatsapp import enviar_whatsapp

    data_livre = timezone.datetime.fromisoformat(data_livre_str).date()
    logger.info(f"[JOB ESPERA] Vaga liberada para procedimento {procedimento_id} na data {data_livre}.")

    interessados = ListaEspera.objects.filter(
        procedimento_id=procedimento_id,
        data_desejada=data_livre,
        notificado=False
    ).select_related('cliente', 'procedimento')

    for espera in interessados:
        mensagem = (
            f"Ola {espera.cliente.nome_completo}! "
            f"Uma vaga para {espera.procedimento.nome} ficou disponivel "
            f"no dia {data_livre.strftime('%d/%m/%Y')}. "
            f"Acesse shivazen.com/agendamento para reservar! "
            f"Shiva Zen"
        )
        enviar_whatsapp(espera.cliente.telefone, mensagem)
        espera.notificado = True
        espera.save()


@shared_task
def job_pesquisa_satisfacao_24h():
    """
    Busca atendimentos REALIZADOS ha mais de 24h sem avaliacao
    e envia pesquisa NPS via WhatsApp.
    """
    from .utils.whatsapp import enviar_whatsapp

    limite = timezone.now() - timedelta(days=1)

    agendamentos = Atendimento.objects.filter(
        status_atendimento='REALIZADO',
        data_hora_fim__lte=limite,
        avaliacaonps__isnull=True
    ).select_related('cliente', 'procedimento')

    logger.info(f"[JOB NPS] {agendamentos.count()} atendimentos sem avaliacao.")

    for agendamento in agendamentos:
        AvaliacaoNPS.objects.create(atendimento=agendamento, nota=0)

        mensagem = (
            f"Ola {agendamento.cliente.nome_completo}! "
            f"Como foi seu atendimento de {agendamento.procedimento.nome}? "
            f"Avalie de 1 a 5 respondendo esta mensagem. "
            f"Sua opiniao e muito importante para nos! "
            f"Shiva Zen"
        )
        enviar_whatsapp(agendamento.cliente.telefone, mensagem)
