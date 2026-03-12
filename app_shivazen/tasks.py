from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from .models import Atendimento

import logging
logger = logging.getLogger(__name__)

@shared_task
def job_enviar_lembrete_dia_seguinte():
    """
    Busca todos os atendimentos marcados para amanhã com status AGENDADO
    e simula o envio de um WhatsApp de confirmação.
    """
    amanha = timezone.now().date() + timedelta(days=1)
    
    agendamentos = Atendimento.objects.filter(
        data_hora_inicio__date=amanha,
        status_atendimento='AGENDADO'
    ).select_related('cliente', 'profissional')

    logger.info(f"[JOB LEMBRETE] Iniciando envio de lembretes para {agendamentos.count()} agendamentos.")

    for agendamento in agendamentos:
        telefone = agendamento.cliente.telefone
        nome = agendamento.cliente.nome_completo
        
        # Aqui integrariamos a API (ex: Z-API / Evolution / Twilio)
        logger.info(f" -> [WHATSAPP SIMULADO] Enviando zap para {nome} ({telefone}): 'Olá, confirmando seu agendamento amanhã às {agendamento.data_hora_inicio.strftime('%H:%M')} no ShivaZen!'")

from .models import Atendimento, ListaEspera, AvaliacaoNPS

@shared_task
def job_notificar_fila_espera(procedimento_id, data_livre_str):
    """
    Acionado quando alguém cancela um agendamento. Avisa quem está na fila.
    """
    data_livre = timezone.datetime.fromisoformat(data_livre_str).date()
    logger.info(f"[JOB ESPERA] Vaga liberada para procedimento {procedimento_id} na data {data_livre}. Buscando interessados...")
    
    # Buscar clientes na fila que querem este procedimento nesta data e ainda não foram notificados
    interessados = ListaEspera.objects.filter(
        procedimento_id=procedimento_id,
        data_desejada=data_livre,
        notificado=False
    )

    for espera in interessados:
        telefone = espera.cliente.telefone
        nome = espera.cliente.nome_completo
        
        # Simula envio via API
        logger.info(f" -> [WHATSAPP SIMULADO] VAGA LIBERADA disparado para fila: {nome} ({telefone})! 'Vaga para {espera.procedimento.nome} dia {data_livre.strftime('%d/%m')}!'")
        
        # Marca como notificado
        espera.notificado = True
        espera.save()


@shared_task
def job_pesquisa_satisfacao_24h():
    """
    Busca atendimentos que ocorreram HÁ MAIS DE 24h (status REALIZADO)
    e envia o link de NPS. Verifica para não mandar duas vezes no AvaliacaoNPS.
    """
    limite = timezone.now() - timedelta(days=1)
    
    agendamentos = Atendimento.objects.filter(
        status_atendimento='REALIZADO',
        data_hora_fim__lte=limite,
        avaliacaonps__isnull=True # Só os que ainda não geraram avaliação
    )
    
    logger.info(f"[JOB NPS] Buscando atendimentos SEM AVALIAÇÃO finalizados até {limite.strftime('%d/%m %H:%M')}. Encontrados: {agendamentos.count()}")

    for agendamento in agendamentos:
        # Cria a avaliação pendente e envia msg
        AvaliacaoNPS.objects.create(atendimento=agendamento, nota=0)
        
        telefone = agendamento.cliente.telefone
        nome = agendamento.cliente.nome_completo
        logger.info(f" -> [WHATSAPP SIMULADO] PESQUISA DE SATISFAÇÃO enviada para {nome} ({telefone}): 'Como foi seu atendimento de ontem? Avalie de 1 a 5!'")

