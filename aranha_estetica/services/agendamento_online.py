"""Caso de uso: pedido de agendamento pelo site (wizard publico, sem login).

Identidade = celular verificado por SMS — o gate do OTP fica no form (forms/agendamento.py), que
conhece a sessao. Aqui fica a regra, testavel sem HTTP e reutilizavel por outra porta. Ordem:

1. termo LGPD vigente (sem ele nao ha prova do aceite: nada e gravado);
2. horario (``agenda.validar_horario``: habilitacao, futuro, feriado/recesso e o slot oferecido);
3. fichas de anamnese do procedimento (schema + obrigatorias) e, havendo resposta, o
   consentimento especifico de dado de saude (LGPD art. 11);
4. termos de procedimento que a titular ainda nao aceitou (services/termos) — marcados no wizard;
5. e-mail que ja e de outro cadastro (evita sequestro e IntegrityError);
6. lock do horario no cache: serializa confirmacoes simultaneas (depois o banco decide);
7. numa transacao: cadastro da titular, bloqueio online, opt-ins de comunicacao, reserva com o
   preco da data, e-mail (APROVACAO p/ a profissional ou CONFIRMACAO p/ a cliente — D8), aceites
   com prova e fichas com a trilha do art. 11.

Toda recusa e ``RegraDeNegocio`` com ``codigo``; a view so mostra ``mensagem``.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

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
from ..utils.email import email_configurado
from ..utils.formatos import formatar_brl
from . import agenda, comunicacao, consentimentos, termos
from .agendamento import formatar_data_hora, formatar_valor_atendimento
from .anamnese import validar_respostas

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

MSG_BLOQUEADO_ONLINE = (
    'Seu cadastro está com agendamento online suspenso. '
    'Fale conosco pelo WhatsApp para marcar seu horário.'
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

# Lock do horario no cache (s): so cobre a janela entre as checagens e o INSERT
TRAVA_HORARIO_SEGUNDOS = 30


@dataclass(frozen=True)
class PedidoAgendamento:
    """O que a cliente pediu no wizard, ja limpo (``AgendamentoPublicoForm.pedido()``)."""
    nome: str
    telefone: str                      # so digitos — o celular verificado por SMS nesta sessao
    data_nascimento: date
    email: str | None
    procedimento: Procedimento
    profissional: Profissional
    inicio: datetime                   # aware
    # campo do wizard (consentimentos.PARES) -> marcado
    consents: Mapping[str, bool] = field(default_factory=dict)
    # True = os checkboxes mostravam o estado do cadastro (prefill do OTP): so entao desmarcar revoga
    consents_sincronizados: bool = False
    termos_marcados: frozenset[int] = frozenset()   # ids das VersaoTermo marcadas no wizard
    # {id do formulario (str): {chave do schema: resposta}} — o JSON do wizard, ainda nao validado
    anamneses: Mapping[str, Any] = field(default_factory=dict)
    consent_saude: bool = False


def solicitar(pedido: PedidoAgendamento, *, request: HttpRequest | None = None) -> Atendimento:
    """Cria o atendimento do pedido feito pelo site ou levanta ``RegraDeNegocio``.

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

    agenda.validar_horario(pedido.profissional, pedido.procedimento, pedido.inicio)

    fichas = fichas_respondidas(pedido.anamneses, pedido.procedimento)
    if fichas and not pedido.consent_saude:
        # Dado de saude (LGPD art. 11): nada e gravado sem o consentimento destacado.
        raise RegraDeNegocio(MSG_CONSENT_SAUDE, codigo='consent_saude_ausente')

    titular = Cliente.objects.filter(telefone=pedido.telefone).first()
    termos_aceitos = _termos_aceitos(pedido, titular)

    if pedido.email and Cliente.objects.filter(email__iexact=pedido.email).exclude(
            telefone=pedido.telefone).exists():
        raise RegraDeNegocio(MSG_EMAIL_DE_OUTRO, codigo='email_de_outro_cadastro')

    with _horario_travado(pedido.profissional, pedido.inicio), transaction.atomic():
        cliente, cadastro_novo = _cadastro_da_titular(pedido)
        # Opt-ins -> historico (prova: texto do checkbox, IP, user-agent). Concede ao marcar;
        # revoga ao desmarcar SO se o checkbox mostrava o estado do cadastro. Ultimo registro manda.
        consentimentos.registrar_do_wizard(
            cliente, dict(pedido.consents), sincronizados=pedido.consents_sincronizados,
            cadastro_novo=cadastro_novo, request=request,
        )
        # Conflito com lock + preco da data (promocao do dia, o mesmo do resumo do wizard)
        atendimento = agenda.reservar(
            cliente=cliente, profissional=pedido.profissional, procedimento=pedido.procedimento,
            inicio=pedido.inicio, status=agenda.status_inicial_publico(cliente),
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
        for termo in termos_aceitos:
            AceiteTermo.registrar(cliente, termo, request, atendimento)
        _gravar_fichas(cliente, atendimento, fichas, request=request)
    return atendimento


# ─── Fichas de anamnese (dado de saude) ─────────────────────────────────────

def _preenchida(valor) -> bool:
    if isinstance(valor, list):
        return any(str(v).strip() for v in valor)
    return valor is not None and str(valor).strip() != ''


def fichas_respondidas(respostas: Mapping[str, Any], procedimento) -> list[tuple[FormularioAnamnese, dict]]:
    """[(formulario, respostas limpas)] das fichas do procedimento que vao ser gravadas.

    So as ANAMNESE ativas que valem p/ o procedimento (FormularioAnamnese.aplicaveis_a); resposta
    de outro formulario e ignorada. Ficha obrigatoria e sempre validada; a opcional so quando a
    cliente comecou a responder. Conteudo validado contra o schema (services.anamnese).
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
        if not (formulario.obrigatorio or any(_preenchida(v) for k, v in brutas.items() if k in chaves)):
            continue
        limpas, erros = validar_respostas(schema, brutas)
        if erros:
            raise RegraDeNegocio(
                f'Questionário "{formulario.nome}": {" ".join(erros[:3])}', codigo='ficha_invalida',
            )
        if limpas:
            fichas.append((formulario, limpas))
    return fichas


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

def _termos_aceitos(pedido: PedidoAgendamento, titular: Cliente | None) -> list[VersaoTermo]:
    """Termos de procedimento marcados no wizard (o proprio + o geral, services/termos).

    Recusa se faltar um que a titular ainda nao aceitou; versao aceita antes nao e exigida de novo
    (o aceite fica no wizard: o link por e-mail nao chegava a quem nao informa e-mail).
    """
    aplicaveis = list(termos.termos_aplicaveis(pedido.procedimento).filter(tipo='PROCEDIMENTO'))
    if not aplicaveis:
        return []
    pendentes = (
        {t.pk for t in termos.termos_pendentes(titular, pedido.procedimento, so_procedimento=True)}
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
    if cliente.nome != pedido.nome:
        cliente.nome = pedido.nome
        alterados.append('nome')
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
