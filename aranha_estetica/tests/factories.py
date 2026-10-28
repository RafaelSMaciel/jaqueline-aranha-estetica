"""Helpers para montar objetos de teste sem repetir boilerplate."""
import itertools
from datetime import time, timedelta
from decimal import Decimal

from django.utils import timezone

from aranha_estetica.models import (
    AgendaExcecao,
    AgendaHorario,
    Atendimento,
    Cliente,
    ItemPacote,
    MovimentoComissao,
    Pacote,
    CompraPacote,
    Preco,
    Procedimento,
    Profissional,
    Habilitacao,
)


def criar_profissional(nome='Dra. Ana', **kwargs):
    prof = Profissional.objects.create(nome=nome, ativo=kwargs.pop('ativo', True), **kwargs)
    # Grade padrao: todos os dias 09h-18h (dia_semana ISO 1..7)
    for dia in range(1, 8):
        AgendaHorario.objects.create(
            profissional=prof,
            dia_semana=dia,
            hora_inicio=time(9, 0),
            hora_fim=time(18, 0),
        )
    return prof


def envelhecer_trilha(model, pk, criado_em):
    """Backdata criado_em de linha append-only (log_auditoria, historico_alteracao) p/ testar a
    purga. No PG o tg_<tabela>_imutavel (0059) recusa o UPDATE: sai do ar so neste UPDATE (DDL
    transacional — volta com o rollback do teste)."""
    from django.db import connection

    tabela = model._meta.db_table
    if connection.vendor != 'postgresql':
        model._base_manager.filter(pk=pk).update(criado_em=criado_em)
        return
    with connection.cursor() as cursor:
        # FKs deferidas do teste pendentes impedem o ALTER TABLE: checa agora e volta a deferir
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        cursor.execute(f'ALTER TABLE {tabela} DISABLE TRIGGER tg_{tabela}_imutavel')
        cursor.execute(f'UPDATE {tabela} SET criado_em = %s WHERE id = %s', [criado_em, pk])
        cursor.execute(f'ALTER TABLE {tabela} ENABLE TRIGGER tg_{tabela}_imutavel')
        cursor.execute('SET CONSTRAINTS ALL DEFERRED')


def limpar_feriados():
    """Feriados/recessos (FOLGA da clinica: semeados pela 0012 e pelo
    carregar_feriados) podem cair na data do teste."""
    AgendaExcecao.objects.filter(profissional__isnull=True, tipo=AgendaExcecao.TIPO_FOLGA).delete()


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


# Contador ilimitado (itertools.count) — evita StopIteration de um iter(range(...)) finito.
_seq_telefone = itertools.count()


def criar_cliente(nome='Maria Silva', telefone=None, **kwargs):
    """Cliente de teste. Opt-ins com o nome do checkbox do wizard
    (consent_email_marketing=True, consent_whatsapp_nps=True,
    consent_whatsapp_confirmacao=True) viram concessoes no historico
    (consentimento, origem AGENDAMENTO_ONLINE)."""
    from aranha_estetica.services import consentimentos

    opt_ins = {campo: kwargs.pop(campo) for campo in list(kwargs) if campo in consentimentos.PARES}
    # telefone unico por chamada — uq_cliente_telefone_ativo (fase 3; nome desde a 0055)
    if telefone is None:
        telefone = f'179{90000 + next(_seq_telefone):08d}'
    cliente = Cliente.objects.create(
        nome=nome,
        telefone=telefone,
        ativo=kwargs.pop('ativo', True),
        **kwargs,
    )
    for campo, concedido in opt_ins.items():
        if concedido:
            conceder(cliente, campo)
    return cliente


def conceder(cliente, campo, *, origem='AGENDAMENTO_ONLINE', registrado_em=None, ip=None, user_agent=''):
    """Concessao de um opt-in (nome do checkbox do wizard) no historico."""
    from aranha_estetica.models import Consentimento
    from aranha_estetica.services import consentimentos

    finalidade, canal = consentimentos.PARES[campo]
    return Consentimento.objects.create(
        cliente=cliente, finalidade=finalidade, canal=canal, concedido=True, origem=origem,
        texto=consentimentos.TEXTOS_WIZARD[campo], ip_origem=ip, user_agent=user_agent,
        registrado_em=registrado_em or timezone.now(),
    )


def criar_atendimento(cliente, profissional, procedimento, data_hora=None, status='AGENDADO'):
    duracao = timedelta(minutes=procedimento.duracao_minutos)
    if data_hora is None:
        base = timezone.now() + timedelta(days=1)
        data_hora = base.replace(hour=10, minute=0, second=0, microsecond=0)
        # Sem horario explicito, um 2o atendimento ATIVO do mesmo profissional
        # vai p/ o proximo horario livre: no Postgres o EXCLUDE
        # ex_atendimento_sobreposicao recusaria o mesmo slot (SQLite nao pega).
        if status in Atendimento.STATUS_ATIVOS:
            while Atendimento.objects.conflito_com(
                profissional, data_hora, data_hora + duracao,
            ).exists():
                data_hora += duracao
    return Atendimento.objects.create(
        cliente=cliente,
        profissional=profissional,
        procedimento=procedimento,
        data_hora_inicio=data_hora,
        data_hora_fim=data_hora + duracao,
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
    """Venda pelo caminho real (CompraPacote.vender): nome e itens congelados. Pacote sem item
    (nao vendavel) ganha um procedimento de 4 sessoes antes — o teste so queria "uma venda"."""
    if not pacote.itens.exists():
        ItemPacote.objects.create(pacote=pacote, procedimento=criar_procedimento(nome=f'Sessao {pacote.nome}'),
                                  quantidade_sessoes=4)
    return CompraPacote.vender(
        pacote, cliente, pacote.preco_total if valor_pago is None else valor_pago, status=status,
    )


def criar_comissao(atendimento, valor='50.00', status=MovimentoComissao.STATUS_PENDENTE,
                   profissional=None, regra=None, usuario=None, criado_em=None):
    """CREDITO de comissao + os lancamentos do status pedido (livro-razao, 0053):
    PAGA = + PAGAMENTO; ESTORNADA = + ESTORNO; 'PAGA_ESTORNADA' = PAGAMENTO e ESTORNO.
    ``criado_em`` data o credito (os lancamentos vem 1 minuto depois, cada)."""
    prof = profissional or atendimento.profissional
    criado_em = criado_em or timezone.now()
    credito = MovimentoComissao.objects.create(
        tipo=MovimentoComissao.TIPO_CREDITO, profissional=prof, atendimento=atendimento,
        regra=regra, valor=Decimal(valor), criado_em=criado_em,
    )
    derivados = {
        MovimentoComissao.STATUS_PENDENTE: (),
        MovimentoComissao.STATUS_PAGA: (MovimentoComissao.TIPO_PAGAMENTO,),
        MovimentoComissao.STATUS_ESTORNADA: (MovimentoComissao.TIPO_ESTORNO,),
        'PAGA_ESTORNADA': (MovimentoComissao.TIPO_PAGAMENTO, MovimentoComissao.TIPO_ESTORNO),
    }[status]
    for minutos, tipo in enumerate(derivados, start=1):
        MovimentoComissao.objects.create(
            tipo=tipo, movimento_origem=credito, profissional=prof, atendimento=atendimento,
            valor=credito.valor, usuario_autor=usuario, criado_em=criado_em + timedelta(minutes=minutos),
        )
    return credito


# ─── Links publicos (link_acesso, so o hash do token no banco) ─────────────
_PADRAO = object()


def _expira_padrao(finalidade, alvo):
    """Mesma validade que o codigo grava na emissao (services/termos, tasks, whatsapp...)."""
    from aranha_estetica.constants import VALIDADE_LINK_FORMULARIO, VALIDADE_LINK_NPS
    from aranha_estetica.models import LinkAcesso

    if finalidade == LinkAcesso.NPS:
        return alvo.criado_em + VALIDADE_LINK_NPS
    if finalidade == LinkAcesso.TERMO:
        return alvo.atendimento.data_hora_fim
    if finalidade == LinkAcesso.CONFIRMAR_PRESENCA:
        return alvo.atendimento.data_hora_inicio
    if finalidade == LinkAcesso.GERENCIAR_AGENDAMENTO:
        return alvo.data_hora_inicio
    if finalidade == LinkAcesso.FORMULARIO:
        return timezone.now() + VALIDADE_LINK_FORMULARIO
    return None


def criar_link(finalidade, alvo, token=None, *, expira_em=_PADRAO, **campos):
    """LinkAcesso com token CONHECIDO (o servico sorteia). Devolve o token em claro."""
    from aranha_estetica.models import LinkAcesso
    from aranha_estetica.services import links

    token = token or links.gerar_token()
    if expira_em is _PADRAO:
        expira_em = _expira_padrao(finalidade, alvo)
    LinkAcesso.objects.create(
        finalidade=finalidade, token_hash=links.sha256_hex(token), expira_em=expira_em,
        **{LinkAcesso.CAMPO_ALVO[finalidade]: alvo}, **campos,
    )
    return token


_FINALIDADE_DA_NOTIFICACAO = {
    'NPS': 'NPS', 'TERMO': 'TERMO',
    'LEMBRETE': 'CONFIRMAR_PRESENCA', 'LEMBRETE_2H': 'CONFIRMAR_PRESENCA', 'CONFIRMACAO': 'CONFIRMAR_PRESENCA',
}


def criar_notificacao_com_link(atendimento, tipo='LEMBRETE', canal='WHATSAPP', token=None,
                               finalidade=None, expira_em=_PADRAO, **campos):
    """(notificacao, token): Notificacao + o LinkAcesso que a rota publica dela aceita."""
    from aranha_estetica.models import Notificacao

    notif = Notificacao.objects.create(atendimento=atendimento, tipo=tipo, canal=canal, **campos)
    if finalidade is None:
        finalidade = 'TERMO' if (tipo, canal) == ('LEMBRETE', 'EMAIL') else _FINALIDADE_DA_NOTIFICACAO[tipo]
    return notif, criar_link(finalidade, notif, token, expira_em=expira_em)


def ja_comecou(atendimento):
    """Leva o atendimento futuro p/ o passado (mesmo horario, N dias antes): Realizado/Faltou so
    depois do inicio (constants.TOLERANCIA_DESFECHO). Devolve o proprio atendimento."""
    agora = timezone.now()
    if atendimento.data_hora_inicio > agora:
        dias = (atendimento.data_hora_inicio - agora).days + 2
        delta = timedelta(days=dias)
        Atendimento.objects.filter(pk=atendimento.pk).update(
            data_hora_inicio=atendimento.data_hora_inicio - delta, data_hora_fim=atendimento.data_hora_fim - delta)
        atendimento.refresh_from_db()
    return atendimento
