"""Agenda: reservar, reagendar e mover atendimentos — fonte unica das regras de horario.

Antes cada porta (wizard, agendamento pelo painel, reagendamento pelo portal/link, calendario,
retorno automatico) repetia habilitacao, feriado, SlotService, conflito com lock, preco da data e
a traducao do 23P01 — e o calendario tinha regra propria. Aqui:

- ``validar_horario``: profissional habilitado, horario futuro, feriado/recesso da clinica e o
  slot do SlotService (expediente, folga, bloqueio, buffer, antecedencia, limite de dias);
- ``reservar``: na transacao do chamador — conflito com lock, preco da data (promocao), INSERT;
  corrida perdida para o EXCLUDE vira ``HorarioIndisponivel``;
- ``reagendar``: antigo -> REAGENDADO e novo com o preco/promocao combinados (portal e link);
- ``mover``: o arrasto do calendario (edita o horario no lugar), com a mesma validacao.

O que e so de uma porta fica nela: OTP, cadastro e consentimentos do wizard; cliente nova e
valor combinado do painel; link e e-mails do portal.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..excecoes import HorarioIndisponivel, RegraDeNegocio, TransicaoInvalida, eh_sobreposicao
from ..models import AgendaExcecao, Atendimento, RespostaAnamnese
from ..constants import LIMITE_AUTOAPROVACAO
from ..models.agendamentos import STATUS_ATIVOS, STATUS_FINALIZADOS, StatusAtendimento
from ..utils.precos import preco_com_promocao
from .atendimento import ao_criar
from .disponibilidade import profissional_habilitado, slot_disponivel

# Duracao do atendimento de retorno quando o procedimento nao define (= RetornoService).
DURACAO_RETORNO_PADRAO_MINUTOS = 30

MSG_INDISPONIVEL = 'Este horário não está disponível. Escolha outro.'
MSG_CORRIDA = 'Este horário acabou de ser reservado. Escolha outro.'


def duracao(procedimento, *, eh_retorno=False) -> timedelta:
    """Retorno gratuito ocupa so a duracao de retorno; o resto, a do procedimento."""
    if eh_retorno:
        return timedelta(minutes=procedimento.duracao_retorno_minutos or DURACAO_RETORNO_PADRAO_MINUTOS)
    return timedelta(minutes=procedimento.duracao_minutos)


def preco_do_dia(procedimento, profissional, inicio) -> dict:
    """Preco da DATA do atendimento (profissional > base) com a promocao vigente nesse dia — o
    mesmo valor do resumo do wizard e da tela do painel. Campos prontos p/ ``reservar``."""
    valor, promocao, cheio = preco_com_promocao(procedimento, profissional, inicio)
    return {
        'valor_cobrado': valor,
        'valor_original': cheio if promocao else None,
        'promocao': promocao,
        'descricao_preco': f'Promoção {promocao.nome}' if promocao else '',
    }


def _conflito(profissional, inicio, fim, *, ignorar_atendimento_id=None, travar=False) -> bool:
    """Outro atendimento ativo da profissional sobrepoe [inicio, fim). travar=True: linhas em
    select_for_update (a reserva serializa com quem esta reservando a mesma janela)."""
    qs = Atendimento.objects.conflito_com(profissional, inicio, fim).exclude(pk=ignorar_atendimento_id)
    return (qs.select_for_update() if travar else qs).exists()


def _motivo_detalhado(profissional, procedimento, inicio, ignorar_atendimento_id) -> str:
    """Por que o SlotService recusou — so p/ a equipe (painel/calendario); o site publico ve a
    mensagem generica (nao expoe a agenda da profissional)."""
    fim = inicio + duracao(procedimento)
    if _conflito(profissional, inicio, fim, ignorar_atendimento_id=ignorar_atendimento_id):
        return 'Conflito: já existe um atendimento nesse horário para o profissional.'
    bloqueado = any(
        bl.ocorrencias(inicio, fim)
        for bl in AgendaExcecao.objects.ativas().do_profissional_ou_global(profissional.pk)
        .bloqueios_no_intervalo(inicio, fim)
    )
    if bloqueado:
        return 'Conflito: o horário cai dentro de um bloqueio de agenda.'
    folga = (
        AgendaExcecao.objects.ativas().filter(profissional_id=profissional.pk, tipo=AgendaExcecao.TIPO_FOLGA)
        .cobrindo(timezone.localtime(inicio).date()).exists()
    )
    if folga:
        return 'Conflito: o profissional está de folga nesse dia.'
    return ('Fora do horário de atendimento do profissional (expediente, intervalo entre sessões, '
            'antecedência mínima ou dias liberados para agendar).')


def validar_horario(profissional, procedimento, inicio, *, ignorar_atendimento_id=None,
                    checar_habilitacao=True, detalhado=False) -> None:
    """Levanta RegraDeNegocio/HorarioIndisponivel se ``inicio`` nao pode ser agendado.

    detalhado=True (equipe): a mensagem diz o motivo (conflito, bloqueio, folga, expediente).
    """
    if checar_habilitacao and not profissional_habilitado(profissional, procedimento):
        raise RegraDeNegocio('Este profissional não realiza este procedimento.', codigo='nao_habilitado')
    if inicio <= timezone.now():
        raise HorarioIndisponivel('Escolha uma data e horário futuros.', codigo='horario_passado')
    if AgendaExcecao.objects.folga_global_em(timezone.localtime(inicio).date()).exists():
        raise HorarioIndisponivel(
            'Esta data é feriado ou recesso da clínica. Escolha outro dia.', codigo='feriado',
        )
    if not slot_disponivel(profissional, procedimento, inicio, ignorar_atendimento_id=ignorar_atendimento_id):
        mensagem = (
            _motivo_detalhado(profissional, procedimento, inicio, ignorar_atendimento_id)
            if detalhado else MSG_INDISPONIVEL
        )
        raise HorarioIndisponivel(mensagem)


def reservar(*, cliente, profissional, procedimento, inicio, status, preco=None, eh_retorno=False,
             atendimento_origem=None, reagendado_de=None) -> Atendimento:
    """Cria o atendimento na transacao do CHAMADOR (que ja passou por ``validar_horario``).

    preco: campos de ``preco_do_dia`` (None = o preco da data). Conflito visto com a linha
    travada ou corrida perdida p/ o EXCLUDE do PG -> HorarioIndisponivel (a transacao do chamador
    segue utilizavel: o INSERT roda num savepoint).
    """
    fim = inicio + duracao(procedimento, eh_retorno=eh_retorno)
    if _conflito(profissional, inicio, fim, travar=True):
        raise HorarioIndisponivel(MSG_CORRIDA, codigo='horario_ocupado')
    preco = preco_do_dia(procedimento, profissional, inicio) if preco is None else preco
    try:
        with transaction.atomic():
            at = Atendimento.objects.create(
                cliente=cliente, profissional=profissional, procedimento=procedimento,
                data_hora_inicio=inicio, data_hora_fim=fim, status=status,
                eh_retorno=eh_retorno, atendimento_origem=atendimento_origem, reagendado_de=reagendado_de,
                **preco,
            )
    except IntegrityError as exc:
        if eh_sobreposicao(exc):
            raise HorarioIndisponivel(MSG_CORRIDA, codigo='horario_ocupado') from exc
        raise
    ao_criar(at)   # push p/ a profissional (on_commit)
    return at


def status_inicial_publico(cliente) -> str:
    """Status do pedido feito pelo site (D8). Cliente de confianca — ja atendida (1+ realizado),
    sem falta em aberto e com menos de LIMITE_AUTOAPROVACAO horarios futuros — nasce AGENDADO e
    recebe a confirmacao na hora; primeira vez (ou muitos horarios em aberto) fica PENDENTE p/ a
    profissional aprovar."""
    if cliente.pk is None or cliente.faltas_consecutivas or cliente.bloqueado_online:
        return StatusAtendimento.PENDENTE
    historico = Atendimento.objects.filter(cliente=cliente)
    if not historico.filter(status=StatusAtendimento.REALIZADO).exists():
        return StatusAtendimento.PENDENTE
    futuros = historico.filter(status__in=STATUS_ATIVOS, data_hora_inicio__gte=timezone.now()).count()
    return StatusAtendimento.AGENDADO if futuros < LIMITE_AUTOAPROVACAO else StatusAtendimento.PENDENTE


def reagendar(antigo, novo_inicio, profissional, *, por=None) -> Atendimento:
    """Move pelo portal/link: o antigo vira REAGENDADO e nasce um novo (historico preservado).

    Preco/promocao combinados e o vinculo de retorno seguem no novo (sem eh_retorno o retorno
    reagendado viraria sessao normal e geraria outro retorno ao ser realizado). Aprovacao so
    continua valendo se o original ja estava aprovado e a profissional e a mesma; senao volta p/
    a fila (PENDENTE). A ficha de anamnese acompanha o atendimento vivo.
    """
    procedimento = antigo.procedimento
    validar_horario(profissional, procedimento, novo_inicio, ignorar_atendimento_id=antigo.pk)
    with transaction.atomic():
        antigo = Atendimento.objects.select_for_update().select_related('cliente').get(pk=antigo.pk)
        if antigo.status in STATUS_FINALIZADOS:
            raise TransicaoInvalida('Este atendimento já foi processado em outra operação.')
        aprovado = antigo.status in (Atendimento.STATUS_AGENDADO, Atendimento.STATUS_CONFIRMADO)
        novo_status = (
            Atendimento.STATUS_AGENDADO if aprovado and profissional.pk == antigo.profissional_id
            else Atendimento.STATUS_PENDENTE
        )
        # Antigo sai da agenda ANTES do novo entrar (FSM + auditoria): senao o EXCLUDE do PG barra
        # mover p/ janela que sobrepoe o proprio slot antigo. Qualquer recusa desfaz os dois.
        antigo.marcar_reagendado(by_user=por, pela_cliente=True)   # a profissional recebe o aviso
        novo = reservar(
            cliente=antigo.cliente, profissional=profissional, procedimento=procedimento,
            inicio=novo_inicio, status=novo_status, eh_retorno=antigo.eh_retorno,
            atendimento_origem=antigo.atendimento_origem, reagendado_de=antigo,
            preco={
                'valor_cobrado': antigo.valor_cobrado, 'valor_original': antigo.valor_original,
                'promocao': antigo.promocao, 'descricao_preco': antigo.descricao_preco,
            },
        )
        RespostaAnamnese.objects.filter(atendimento=antigo).update(atendimento=novo)
    return novo


def mover(atendimento_id, novo_inicio) -> tuple[Atendimento, object]:
    """Arrasto no calendario: edita o horario no lugar (mesma validacao do resto, com o motivo
    detalhado p/ a equipe). A duracao e o preco combinado ficam. -> (atendimento, inicio antigo)."""
    with transaction.atomic():
        at = (
            Atendimento.objects.select_for_update().select_related('profissional', 'procedimento')
            .filter(pk=atendimento_id).first()
        )
        if at is None:
            raise RegraDeNegocio('Atendimento não encontrado.', codigo='nao_encontrado')
        if at.status not in STATUS_ATIVOS:
            raise TransicaoInvalida(f'Não é possível mover um atendimento {at.get_status_display().lower()}.')
        validar_horario(at.profissional, at.procedimento, novo_inicio, ignorar_atendimento_id=at.pk,
                        checar_habilitacao=False, detalhado=True)
        antigo_inicio = at.data_hora_inicio
        at.data_hora_fim = novo_inicio + (at.data_hora_fim - at.data_hora_inicio)
        at.data_hora_inicio = novo_inicio
        try:
            with transaction.atomic():   # savepoint: corrida com o booking entre a checagem e o save
                at.save(update_fields=['data_hora_inicio', 'data_hora_fim', 'atualizado_em'])
        except IntegrityError as exc:
            if eh_sobreposicao(exc):
                raise HorarioIndisponivel(
                    'Conflito: o horário acabou de ser ocupado. Atualize o calendário.', codigo='horario_ocupado',
                ) from exc
            raise
    return at, antigo_inicio
