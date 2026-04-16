"""Helpers para montar objetos de teste sem repetir boilerplate."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from app_shivazen.models import (
    Atendimento,
    Cliente,
    DisponibilidadeProfissional,
    ItemPacote,
    Pacote,
    PacoteCliente,
    Preco,
    Procedimento,
    Profissional,
    ProfissionalProcedimento,
)


def criar_profissional(nome='Dra. Ana', **kwargs):
    prof = Profissional.objects.create(nome=nome, ativo=kwargs.pop('ativo', True), **kwargs)
    # Disponibilidade padrao: todos os dias 09h-18h
    for dia in range(1, 8):
        DisponibilidadeProfissional.objects.create(
            profissional=prof,
            dia_semana=dia,
            hora_inicio=time(9, 0),
            hora_fim=time(18, 0),
        )
    return prof


def criar_procedimento(nome='Limpeza de Pele', duracao=30, categoria='FACIAL',
                      preco=Decimal('150.00'), profissional=None):
    proc = Procedimento.objects.create(
        nome=nome,
        duracao_minutos=duracao,
        categoria=categoria,
        ativo=True,
    )
    if preco is not None:
        Preco.objects.create(procedimento=proc, valor=preco)
    if profissional:
        ProfissionalProcedimento.objects.create(
            profissional=profissional, procedimento=proc
        )
    return proc


def criar_cliente(nome='Maria Silva', telefone='17999990000', **kwargs):
    return Cliente.objects.create(
        nome_completo=nome,
        telefone=telefone,
        ativo=kwargs.pop('ativo', True),
        **kwargs,
    )


def criar_atendimento(cliente, profissional, procedimento, data_hora=None, status='AGENDADO'):
    if data_hora is None:
        base = timezone.now() + timedelta(days=1)
        data_hora = base.replace(hour=10, minute=0, second=0, microsecond=0)
    return Atendimento.objects.create(
        cliente=cliente,
        profissional=profissional,
        procedimento=procedimento,
        data_hora_inicio=data_hora,
        data_hora_fim=data_hora + timedelta(minutes=procedimento.duracao_minutos),
        status=status,
    )


def criar_pacote(nome='Pacote Glow', preco=Decimal('600.00'),
                 procedimento=None, sessoes=4, validade_meses=12):
    pac = Pacote.objects.create(
        nome=nome,
        preco_total=preco,
        ativo=True,
        validade_meses=validade_meses,
    )
    if procedimento:
        ItemPacote.objects.create(
            pacote=pac, procedimento=procedimento, quantidade_sessoes=sessoes
        )
    return pac


def criar_pacote_cliente(cliente, pacote, valor_pago=None, status='ATIVO'):
    return PacoteCliente.objects.create(
        cliente=cliente,
        pacote=pacote,
        valor_pago=valor_pago or pacote.preco_total,
        status=status,
    )
