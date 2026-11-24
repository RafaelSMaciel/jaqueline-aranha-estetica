"""Caso de uso: pedido de agendamento pelo site (wizard publico, sem login).

Identidade = celular verificado por SMS — o gate do OTP fica no form (forms/agendamento.py), que
conhece a sessao. Aqui fica a regra, testavel sem HTTP e reutilizavel por outra porta.

Um pedido leva de 1 a MAX_TRATAMENTOS_POR_PEDIDO tratamentos (``PedidoAgendamento.itens``), cada um
com o seu horario e a sua profissional: cada item vira um Atendimento, todos na mesma transacao —
ou entram todos ou nenhum. Ordem:

1. termo LGPD vigente (sem ele nao ha prova do aceite: nada e gravado);
2. o conjunto: sem tratamento repetido e sem horarios que se sobreponham (a cliente nao faz dois
   ao mesmo tempo; com a mesma profissional vale tambem o intervalo entre sessoes);
3. horario de cada item (``agenda.validar_horario``: habilitacao, futuro, feriado/recesso e o slot);
4. fichas de anamnese dos procedimentos (schema + obrigatorias que a titular nao tem em dia — a
   ficha vale 12 meses, D14) e, havendo resposta, o consentimento de dado de saude (LGPD art. 11);
5. termos de procedimento que a titular ainda nao aceitou (services/termos) — marcados no wizard;
6. e-mail que ja e de outro cadastro (evita sequestro e IntegrityError);
7. lock de cada horario no cache: serializa confirmacoes simultaneas (depois o banco decide);
8. numa transacao: cadastro da titular, bloqueio online, teto de pedidos em aberto (D8), reserva
   com o preco da data, e-mail (APROVACAO p/ a profissional ou CONFIRMACAO p/ a cliente — D8),
   aceites com prova e fichas com a trilha do art. 11.

Opt-ins de comunicacao (lembrete por WhatsApp, pesquisa, promocoes) NAO sao pedidos aqui: ficam so
em "Meus agendamentos > Minhas preferencias" (decisao do dono, 2026-11-24).

Toda recusa e ``RegraDeNegocio`` com ``codigo``; a view so mostra ``mensagem``.
"""
from __future__ import annotations

import logging
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from ..constants import MAX_ATIVOS_ONLINE, MAX_TRATAMENTOS_POR_PEDIDO
from ..excecoes import RegraDeNegocio
from ..models import (
    AceiteTermo,
    Atendimento,
    Cliente,
    FormularioAnamnese,
    Notificacao,
    Procedimento,
    Profissional,
    RespostaAnamnese,
    VersaoTermo,
)
from ..utils.auditoria import registrar_log
from ..utils.datas import fmt_local
from ..utils.email import email_configurado
from ..utils.formatos import formatar_brl
from . import agenda, anamnese, comunicacao, termos
from .agendamento import formatar_data_hora, formatar_valor_atendimento
from .anamnese import validar_respostas

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

# Diz o motivo e o caminho de revisao: a suspensao automatica (faltas seguidas, MAX_FALTAS_BLOQUEIO)
# e decisao automatizada — a titular pode pedir revisao (LGPD art. 20)
MSG_BLOQUEADO_ONLINE = (
    'O agendamento online do seu cadastro está suspenso — isso acontece depois de faltas '
    'seguidas sem aviso ou por decisão da clínica. Para pedir a revisão ou marcar seu horário, '
    'fale conosco pelo WhatsApp.'
)
MSG_SEM_TERMO_LGPD = (
    'Não foi possível concluir o agendamento online agora. '
    'Fale conosco pelo WhatsApp para marcar seu horário.'
)
MSG_CONSENT_SAUDE = (
    'Para enviar o questionário pré-atendimento, marque a autorização de uso '
    'das suas informações de saúde.'
)
MSG_EMAIL_DE_OUTRO = 'Este e-mail já está vinculado a outro cadastro. Use outro e-mail ou deixe em branco.'
MSG_EM_CONFIRMACAO = 'Este horário está sendo confirmado por outra pessoa. Tente outro.'
MSG_LIMITE_ONLINE = (
    f'Você já tem {MAX_ATIVOS_ONLINE} horários marcados com a gente. '
    'Para marcar mais um, fale conosco pelo WhatsApp.'
)
MSG_LIMITE_COM_PEDIDO = (
    f'Pelo site dá para ter até {MAX_ATIVOS_ONLINE} horários marcados ao mesmo tempo. '
    'Tire um tratamento deste pedido ou fale conosco pelo WhatsApp.'
)
MSG_TRATAMENTOS_DEMAIS = (
    f'Dá para agendar até {MAX_TRATAMENTOS_POR_PEDIDO} tratamentos de uma vez. '
    'Para mais, faça outro pedido ou fale conosco pelo WhatsApp.'
)
MSG_TRATAMENTO_REPETIDO = 'O mesmo tratamento aparece duas vezes no pedido. Escolha cada tratamento uma vez.'
MSG_HORARIOS_SOBREPOSTOS = (
    'Os horários escolhidos se sobrepõem: um tratamento começa antes de o outro terminar. '
    'Escolha outro horário para um deles.'
)

# Lock do horario no cache (s): so cobre a janela entre as checagens e o INSERT
TRAVA_HORARIO_SEGUNDOS = 30


@dataclass(frozen=True)
class ItemPedido:
    """Um tratamento do pedido: o procedimento, quem atende e quando (aware)."""
    procedimento: Procedimento
    profissional: Profissional
    inicio: datetime

    @property
    def fim(self) -> datetime:
        return self.inicio + agenda.duracao(self.procedimento)

    @property
    def buffer(self) -> timedelta:
        return timedelta(minutes=self.procedimento.buffer_minutos or 0)


@dataclass(frozen=True)
class PedidoAgendamento:
    """O que a cliente pediu no wizard, ja limpo (``AgendamentoPublicoForm.pedido()``).

    ``procedimento``/``profissional``/``inicio`` sao o 1o tratamento; ``extras`` os demais do mesmo
    pedido (o wizard deixa escolher varios). ``itens`` devolve todos, na ordem escolhida."""
    nome: str
    telefone: str                      # so digitos — o celular verificado por SMS nesta sessao
    data_nascimento: date
    email: str | None
    procedimento: Procedimento
    profissional: Profissional
    inicio: datetime                   # aware
    extras: tuple[ItemPedido, ...] = ()
    termos_marcados: frozenset[int] = frozenset()   # ids das VersaoTermo marcadas no wizard
    # {id do formulario (str): {chave do schema: resposta}} — o JSON do wizard, ainda nao validado
    anamneses: Mapping[str, Any] = field(default_factory=dict)
    consent_saude: bool = False

    @property
    def itens(self) -> tuple[ItemPedido, ...]:
        return (ItemPedido(self.procedimento, self.profissional, self.inicio), *self.extras)


def solicitar(pedido: PedidoAgendamento, *, request: HttpRequest | None = None) -> Atendimento:
    """Pedido de UM tratamento (ou o 1o atendimento de um pedido com varios): atalho de
    ``solicitar_varios`` p/ quem so precisa do primeiro."""
    return solicitar_varios(pedido, request=request)[0]


def solicitar_varios(pedido: PedidoAgendamento, *, request: HttpRequest | None = None) -> list[Atendimento]:
    """Cria os atendimentos do pedido feito pelo site (um por tratamento, na ordem do pedido) ou
    levanta ``RegraDeNegocio`` — tudo ou nada.

    PENDENTE p/ a profissional aprovar ou AGENDADO direto p/ cliente de confianca
    (``agenda.status_inicial_publico``). ``request`` vai p/ a prova dos aceites e consentimentos
    (IP, user-agent) e p/ a trilha de auditoria.
    """
    termo_lgpd = VersaoTermo.lgpd_vigente()
    if termo_lgpd is None:
        # Sem versao LGPD vigente nao ha o que registrar em AceiteTermo: recusa em vez de
        # gravar o agendamento sem a prova do aceite (Painel > Termos).
        logger.error('booking_sem_termo_lgpd_vigente')
        raise RegraDeNegocio(MSG_SEM_TERMO_LGPD, codigo='sem_termo_lgpd')

    itens = pedido.itens
    validar_conjunto(itens)
    for item in itens:
        agenda.validar_horario(item.profissional, item.procedimento, item.inicio)

    titular = Cliente.objects.filter(telefone=pedido.telefone).first()
    # Ficha obrigatoria respondida nos ultimos 12 meses (D14) nao e exigida de novo — e tem de
    # estar em dia no ULTIMO horario do pedido
    em_dia = anamnese.fichas_em_dia(titular, max(item.inicio for item in itens))
    # {indice do item: [(formulario, respostas)]}: cada ficha e gravada uma vez, no 1o tratamento
    # do pedido em que ela vale
    fichas_por_item: dict[int, list[tuple[FormularioAnamnese, dict]]] = {}
    vistas: set[int] = set()
    for indice, item in enumerate(itens):
        for formulario, respostas in fichas_respondidas(pedido.anamneses, item.procedimento, em_dia=em_dia):
            if formulario.pk not in vistas:
                vistas.add(formulario.pk)
                fichas_por_item.setdefault(indice, []).append((formulario, respostas))
    if fichas_por_item and not pedido.consent_saude:
        # Dado de saude (LGPD art. 11): nada e gravado sem o consentimento destacado.
        raise RegraDeNegocio(MSG_CONSENT_SAUDE, codigo='consent_saude_ausente')

    termos_por_item = [_termos_aceitos(pedido, titular, item.procedimento) for item in itens]

    if pedido.email and Cliente.objects.filter(email__iexact=pedido.email).exclude(
            telefone=pedido.telefone).exists():
        raise RegraDeNegocio(MSG_EMAIL_DE_OUTRO, codigo='email_de_outro_cadastro')

    atendimentos: list[Atendimento] = []
    with ExitStack() as travas:
        # ordem fixa (profissional, horario): dois pedidos com os mesmos slots nao se cruzam
        for item in sorted(itens, key=lambda i: (i.profissional.pk, i.inicio)):
            travas.enter_context(_horario_travado(item.profissional, item.inicio))
        with transaction.atomic():
            cliente, cadastro_novo = _cadastro_da_titular(pedido)
            # linha da cliente travada: pedidos simultaneos serializam
            validar_limite_online(cliente if not cadastro_novo else None, novos=len(itens))
            for indice, item in enumerate(itens):
                # Conflito com lock + preco da data (promocao do dia, o mesmo do resumo do wizard)
                atendimento = agenda.reservar(
                    cliente=cliente, profissional=item.profissional, procedimento=item.procedimento,
                    inicio=item.inicio, status=agenda.status_inicial_publico(cliente),
                )
                if atendimento.status == Atendimento.STATUS_PENDENTE:
                    # Pedido p/ a profissional aprovar (o link do e-mail so leva a agenda do dia)
                    comunicacao.enfileirar_email(atendimento, Notificacao.Tipo.APROVACAO)
                else:
                    # Cliente de confianca (D8): horario ja garantido, confirmacao na hora
                    comunicacao.enfileirar_email(atendimento, Notificacao.Tipo.CONFIRMACAO)

                # Aceites com prova (IP, user-agent, SHA-256 do texto): Politica de Privacidade
                # (versao LGPD vigente) + termo(s) do procedimento marcados no wizard.
                AceiteTermo.registrar(cliente, termo_lgpd, request, atendimento)
                for termo in termos_por_item[indice]:
                    AceiteTermo.registrar(cliente, termo, request, atendimento)
                _gravar_fichas(cliente, atendimento, fichas_por_item.get(indice, []), request=request)
                atendimentos.append(atendimento)
    return atendimentos


def validar_conjunto(itens: Sequence[ItemPedido]) -> None:
    """Os tratamentos de um pedido cabem juntos? Recusa pedido vazio ou acima do teto, tratamento
    repetido e horarios que se sobrepoem.

    A cliente nao faz dois tratamentos ao mesmo tempo (mesmo com profissionais diferentes). Com a
    MESMA profissional conta tambem o intervalo entre sessoes (buffer) — a mesma conta do
    SlotService p/ um atendimento ja marcado: cada horario foi oferecido sem saber do outro.
    """
    if not itens:
        raise RegraDeNegocio('Escolha ao menos um tratamento.', codigo='sem_tratamento')
    if len(itens) > MAX_TRATAMENTOS_POR_PEDIDO:
        raise RegraDeNegocio(MSG_TRATAMENTOS_DEMAIS, codigo='tratamentos_demais')
    if len({item.procedimento.pk for item in itens}) != len(itens):
        raise RegraDeNegocio(MSG_TRATAMENTO_REPETIDO, codigo='tratamento_repetido')
    for i, a in enumerate(itens):
        for b in itens[i + 1:]:
            mesma = a.profissional.pk == b.profissional.pk
            folga_a = a.buffer if mesma else timedelta(0)
            folga_b = b.buffer if mesma else timedelta(0)
            if a.inicio < b.fim + folga_b and b.inicio < a.fim + folga_a:
                raise RegraDeNegocio(MSG_HORARIOS_SOBREPOSTOS, codigo='horarios_sobrepostos')


# ─── Fichas de anamnese (dado de saude) ─────────────────────────────────────

def _preenchida(valor) -> bool:
    if isinstance(valor, list):
        return any(str(v).strip() for v in valor)
    return valor is not None and str(valor).strip() != ''


def fichas_respondidas(respostas: Mapping[str, Any], procedimento, *,
                       em_dia: Collection[int] = ()) -> list[tuple[FormularioAnamnese, dict]]:
    """[(formulario, respostas limpas)] das fichas do procedimento que vao ser gravadas.

    So as ANAMNESE ativas que valem p/ o procedimento (FormularioAnamnese.aplicaveis_a); resposta
    de outro formulario e ignorada. Ficha obrigatoria pendente (fora de ``em_dia``, os ids que a
    titular respondeu dentro da validade — D14) e sempre validada; a opcional, ou a obrigatoria em
    dia, so quando a cliente comecou a responder ("Atualizar"). Conteudo validado contra o schema.
    """
    fichas = []
    formularios = (
        FormularioAnamnese.objects.filter(tipo=FormularioAnamnese.TIPO_ANAMNESE, ativo=True)
        .aplicaveis_a(procedimento).order_by('pk')
    )
    for formulario in formularios:
        brutas = respostas.get(str(formulario.pk))
        if not isinstance(brutas, dict):
            brutas = {}
        schema = formulario.schema_json if isinstance(formulario.schema_json, list) else []
        chaves = {c.get('key') for c in schema if isinstance(c, dict)}
        exigida = formulario.obrigatorio and formulario.pk not in em_dia
        if not (exigida or any(_preenchida(v) for k, v in brutas.items() if k in chaves)):
            continue
        limpas, erros = validar_respostas(schema, brutas)
        if erros:
            raise RegraDeNegocio(
                f'Questionário "{formulario.nome}": {" ".join(erros[:3])}', codigo='ficha_invalida',
            )
        if limpas:
            fichas.append((formulario, limpas))
    return fichas


def fichas_em_dia_do_wizard(cliente: Cliente | None, inicio: datetime | None = None) -> dict[str, str]:
    """{id do formulario: 'dd/mm/aaaa'} das fichas que a titular tem em dia no horario escolhido
    (D14; sem horario, agora): o wizard mostra "Sua ficha de dd/mm esta em dia" e nao a exige."""
    return {str(fid): fmt_local(quando, '%d/%m/%Y') for fid, quando in anamnese.fichas_em_dia(cliente, inicio).items()}


def _gravar_fichas(cliente, atendimento, fichas, *, request) -> None:
    """Respostas + consentimento art. 11 = aceite da versao SAUDE vigente (prova no banco) e
    trilha de auditoria com o texto consentido."""
    if not fichas:
        return
    agora = timezone.now()
    for formulario, respostas in fichas:
        RespostaAnamnese.objects.create(
            formulario=formulario, cliente=cliente, atendimento=atendimento,
            respostas_json=respostas, respondida_em=agora,
        )
    termo_saude, texto_saude = VersaoTermo.texto_saude_vigente()
    aceite_saude = AceiteTermo.registrar(cliente, termo_saude, request, atendimento)
    if aceite_saude is None:
        logger.warning('booking_sem_termo_saude_vigente', extra={'atendimento_id': atendimento.pk})
    registrar_log(
        None, 'Consentimento de dados de saude (LGPD art. 11) no agendamento',
        'atendimento', atendimento.pk,
        detalhes={
            'cliente_id': cliente.pk,
            'formularios': [formulario.pk for formulario, _respostas in fichas],
            'texto': texto_saude,
            'aceite_id': getattr(aceite_saude, 'pk', None),
        },
        request=request,
    )


# ─── Termos, cadastro e horario ─────────────────────────────────────────────

def validar_limite_online(cliente: Cliente | None, *, novos: int = 1) -> None:
    """Recusa o pedido pelo site que passaria de MAX_ATIVOS_ONLINE horarios futuros ativos (D8): o
    PENDENTE trava a agenda, entao sem teto um celular verificado reservaria a semana inteira. Conta
    todos os canais (o atendimento nao guarda a origem); a recepcao segue marcando pelo painel.

    ``novos``: quantos tratamentos este pedido marca. ``cliente=None`` = cadastro novo (nada marcado)."""
    ativos = 0 if cliente is None else Atendimento.objects.filter(
        cliente=cliente, status__in=Atendimento.STATUS_ATIVOS, data_hora_inicio__gte=timezone.now(),
    ).count()
    if ativos >= MAX_ATIVOS_ONLINE:
        raise RegraDeNegocio(MSG_LIMITE_ONLINE, codigo='limite_online')
    if ativos + novos > MAX_ATIVOS_ONLINE:
        raise RegraDeNegocio(MSG_LIMITE_COM_PEDIDO, codigo='limite_online')


def _termos_aceitos(pedido: PedidoAgendamento, titular: Cliente | None,
                    procedimento: Procedimento | None = None) -> list[VersaoTermo]:
    """Termos de procedimento marcados no wizard (o proprio + o geral, services/termos) p/ um
    tratamento do pedido (padrao: o 1o).

    Recusa se faltar um que a titular ainda nao aceitou; versao aceita antes nao e exigida de novo
    (o aceite fica no wizard: o link por e-mail nao chegava a quem nao informa e-mail).
    """
    procedimento = procedimento or pedido.procedimento
    aplicaveis = list(termos.termos_aplicaveis(procedimento).filter(tipo='PROCEDIMENTO'))
    if not aplicaveis:
        return []
    pendentes = (
        {t.pk for t in termos.termos_pendentes(titular, procedimento, so_procedimento=True)}
        if titular is not None else {t.pk for t in aplicaveis}
    )
    faltando = next(
        (t for t in aplicaveis if t.pk in pendentes and t.pk not in pedido.termos_marcados), None,
    )
    if faltando is not None:
        raise RegraDeNegocio(
            f'Leia e aceite o termo "{faltando.titulo}" para concluir o agendamento.', codigo='termo_pendente',
        )
    return [t for t in aplicaveis if t.pk in pedido.termos_marcados]


def _cadastro_da_titular(pedido: PedidoAgendamento) -> tuple[Cliente, bool]:
    """(cliente, cadastro_novo) do celular verificado, com a linha travada ate o fim da transacao.

    O agendamento e sempre da TITULAR do celular (D16): cadastro existente mantem o nome — nome
    diferente no POST e ignorado e vai p/ o log (renomear so pelo painel, com auditoria). Assim
    quem agenda p/ outra pessoa nao mistura a ficha de saude dela no cadastro da titular.
    Cadastro com agendamento online suspenso (3 faltas ou bloqueio pelo painel) ou inativo recusa.
    """
    cliente, criado = Cliente.objects.select_for_update().get_or_create(
        telefone=pedido.telefone,
        defaults={
            'nome': pedido.nome, 'data_nascimento': pedido.data_nascimento,
            'email': pedido.email, 'ativo': True,
        },
    )
    if criado:
        return cliente, True
    if cliente.bloqueado_online or not cliente.ativo:
        raise RegraDeNegocio(MSG_BLOQUEADO_ONLINE, codigo='bloqueado_online')
    alterados = []
    if not (cliente.nome or '').strip():
        cliente.nome = pedido.nome
        alterados.append('nome')
    elif cliente.nome != pedido.nome:
        logger.info('booking_nome_divergente', extra={'cliente_id': cliente.pk})
    if not cliente.data_nascimento and pedido.data_nascimento:
        cliente.data_nascimento = pedido.data_nascimento
        alterados.append('data_nascimento')
    if pedido.email and cliente.email != pedido.email:
        # Telefone acabou de ser provado por SMS: o e-mail informado pela dona do celular
        # substitui o do cadastro (inclusive um plantado por terceiro). Em branco = mantem.
        cliente.email = pedido.email
        alterados.append('email')
    if alterados:
        cliente.save()
    return cliente, False


def resumo_confirmacao(atendimento: Atendimento, *, reagendamento: bool = False) -> dict:
    """Dados da pagina de sucesso (sessao) do pedido pelo site e do reagendamento pelo portal.

    Uma regra so p/ as duas portas: o valor e o de ``formatar_valor_atendimento`` (retorno sem
    custo; sessao que um pacote dela cobre aparece como do pacote, nunca com o preco cheio — D21)
    e o cheio riscado + nome da promocao so quando a promocao de fato reduziu o valor cobrado.
    """
    valor = formatar_valor_atendimento(atendimento)
    em_pacote = valor.startswith('Sessão do pacote')
    em_promocao = bool(
        atendimento.promocao_id and not em_pacote and not atendimento.eh_retorno
        and atendimento.valor_original is not None and atendimento.valor_cobrado is not None
        and atendimento.valor_original > atendimento.valor_cobrado
    )
    cliente = atendimento.cliente
    return {
        'nome': cliente.nome,
        'procedimento': atendimento.procedimento.nome,
        'profissional': atendimento.profissional.nome,
        'data_hora': formatar_data_hora(atendimento.data_hora_inicio),
        'valor': valor,
        'valor_cheio': formatar_brl(atendimento.valor_original) if em_promocao else '',
        'promocao': atendimento.promocao.nome if em_promocao and atendimento.promocao else '',
        'pendente': atendimento.status == Atendimento.STATUS_PENDENTE,
        'reagendamento': reagendamento,
        # So promete acompanhamento por e-mail se o backend entrega de fato
        'email': bool(cliente.email) and email_configurado(),
    }


def resumo_do_pedido(atendimentos: Sequence[Atendimento]) -> dict:
    """Dados da pagina de sucesso de um pedido com 1+ tratamentos: o resumo do 1o (as chaves de
    sempre) + ``itens`` (um resumo por tratamento, em ordem de horario) e ``pendente`` se algum
    ainda espera a aprovacao."""
    ordenados = sorted(atendimentos, key=lambda a: a.data_hora_inicio)
    itens = [resumo_confirmacao(atendimento) for atendimento in ordenados]
    dados = dict(itens[0])
    dados['itens'] = itens
    dados['pendente'] = any(item['pendente'] for item in itens)
    return dados


@contextmanager
def _horario_travado(profissional: Profissional, inicio: datetime) -> Iterator[None]:
    """Lock curto do horario no cache: serializa confirmacoes simultaneas do mesmo slot; depois
    o banco decide (conflito com select_for_update + EXCLUDE do PG em ``agenda.reservar``)."""
    chave = f'booking_slot:{profissional.pk}:{inicio.isoformat()}'
    if not cache.add(chave, '1', timeout=TRAVA_HORARIO_SEGUNDOS):
        raise RegraDeNegocio(MSG_EM_CONFIRMACAO, codigo='horario_em_confirmacao')
    try:
        yield
    finally:
        cache.delete(chave)
