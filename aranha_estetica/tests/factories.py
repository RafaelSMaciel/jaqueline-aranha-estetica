"""Helpers para montar objetos de teste sem repetir boilerplate."""
from datetime import time, timedelta
from decimal import Decimal

from django.utils import timezone

from aranha_estetica.models import (
    Atendimento,
    Cliente,
    DisponibilidadeProfissional,
    ItemPacote,
    Pacote,
    CompraPacote,
    Preco,
    Procedimento,
    Profissional,
    Habilitacao,
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
        Habilitacao.objects.create(
            profissional=profissional, procedimento=proc
        )
    return proc


_seq_telefone = iter(range(100000))


def criar_cliente(nome='Maria Silva', telefone=None, **kwargs):
    # telefone unico por chamada — uniq_cliente_telefone_ativo (fase 3)
    if telefone is None:
        telefone = f'179{90000 + next(_seq_telefone):08d}'
    return Cliente.objects.create(
        nome=nome,
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


def criar_compra_pacote(cliente, pacote, valor_pago=None, status='ATIVO'):
    return CompraPacote.objects.create(
        cliente=cliente,
        pacote=pacote,
        valor_pago=valor_pago or pacote.preco_total,
        status=status,
    )
