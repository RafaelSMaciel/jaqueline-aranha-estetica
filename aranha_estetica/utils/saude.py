"""Alertas de saude do cliente p/ quem executa o procedimento.

Junta o que a equipe preencheu no Prontuario (campos clinicos + respostas do
questionario do prontuario) com o que a propria cliente declarou nas fichas
de anamnese (RespostaAnamnese) — antes a alergia declarada no agendamento
nunca chegava ao prontuario/portal/agenda.

Perguntas de qualquer formulario (ANAMNESE ou PRONTUARIO, spec padrao-sql §4.d)
seguem a mesma regra por pergunta (``deve_alertar``): ``alerta_saude: true``
alerta sempre que a resposta for positiva, ``false`` nunca, ausente =
heuristica ``_RISCO_RX`` sobre ``key + label``.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# chave/label de campo de anamnese que indica risco (sem acento, minusculo)
_RISCO_RX = re.compile(
    r'alerg|contraindic|medicament|remedio|gestan|gravid|amament|lactan|'
    r'doenca|cirurg|anticoag|diabet|hipertens|cardi|marca.?passo|queloide|'
    r'retino|roacutan|herpes|epileps|autoimun|cancer|oncolog',
)
_NEGATIVOS = {'', 'nao', 'não', 'n', 'no', 'false', '0', 'nenhum', 'nenhuma', 'nada', 'sem', '-', 'nao possuo', 'não possuo'}


def _sem_acento(txt: str) -> str:
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', txt or '') if unicodedata.category(c) != 'Mn').lower()


def _valor_positivo(valor) -> str | None:
    """Texto do valor se indicar algo a alertar; None se vazio/negativo."""
    if valor is None or valor is False:
        return None
    if valor is True:
        return 'Sim'
    if isinstance(valor, (list, tuple)):
        itens = [str(v).strip() for v in valor if str(v).strip() and _sem_acento(str(v)).strip() not in _NEGATIVOS]
        return ', '.join(itens) or None
    txt = str(valor).strip()
    if _sem_acento(txt) in _NEGATIVOS:
        return None
    return txt[:300]


def rotulo_da_chave(key) -> str:
    """Label derivado da key (pergunta que saiu do formulario, sufixo '_x' da 0036)."""
    return str(key).replace('_', ' ').strip().capitalize()


def deve_alertar(pergunta: dict | None, key, label) -> bool:
    """A resposta (positiva) desta pergunta vira alerta? alerta_saude > heuristica."""
    marcado = pergunta.get('alerta_saude') if isinstance(pergunta, dict) else None
    if isinstance(marcado, bool):
        return marcado
    return bool(_RISCO_RX.search(_sem_acento(f'{key} {label}')))


def perguntas_prontuario() -> list[dict]:
    """Perguntas do questionario do prontuario — [{key, label, tipo, ...}].

    Vivem no formulario tipo PRONTUARIO ativo (FormularioAnamnese, no maximo 1;
    editado no CRUD de anamneses). Antes da 0051 eram o JSON
    Configuracao 'prontuario_perguntas' ({chave, texto, tipo}).
    """
    from ..models import FormularioAnamnese

    form = FormularioAnamnese.objects.prontuario_ativo()
    perguntas = form.schema_json if form is not None else []
    return [p for p in (perguntas if isinstance(perguntas, list) else []) if isinstance(p, dict) and p.get('key')]


def alertas_saude(cliente, limite_fichas: int = 5, perguntas: list[dict] | None = None) -> list[dict]:
    """[{'label', 'valor', 'origem'}] com alergias/contraindicacoes/etc. do cliente.

    origem: 'prontuario' ou 'anamnese' (ficha respondida pela cliente). Considera
    as `limite_fichas` fichas de ANAMNESE mais recentes respondidas. Lista de
    clientes: alertas_em_lote (mesmas consultas p/ 1 ou 50).
    """
    if cliente is None or not getattr(cliente, 'pk', None):
        return []
    return alertas_em_lote([cliente.pk], limite_fichas, perguntas).get(cliente.pk, [])


def alertas_em_lote(cliente_ids, limite_fichas: int = 5,
                    perguntas: list[dict] | None = None) -> dict[int, list[dict]]:
    """{cliente_id: alertas} so p/ quem tem alerta — 3 consultas no maximo, qualquer que seja o
    tamanho da lista (prontuarios, fichas e as perguntas do prontuario, essas so se precisar).

    Antes cada cliente custava 2-3 consultas (o painel com 25 linhas passava de 70).
    `perguntas` = perguntas_prontuario() ja lidas.
    """
    from django.db.models import F, Window
    from django.db.models.functions import RowNumber

    from ..models import Prontuario, RespostaAnamnese

    ids = {int(i) for i in cliente_ids if i}
    if not ids:
        return {}
    # revogacao do consentimento de saude (cliente.saude_revogada_em) vem no JOIN das mesmas consultas
    revogados: dict[int, object] = {}
    prontuarios = {}
    for p in (Prontuario.objects.filter(cliente_id__in=ids)
              .only('cliente_id', 'alergias', 'contraindicacoes', 'medicamentos_uso', 'respostas_extras')
              .annotate(revogada_em=F('cliente__saude_revogada_em'))):
        prontuarios[p.cliente_id] = p
        if p.revogada_em is not None:
            revogados[p.cliente_id] = p.revogada_em
    fichas: dict[int, list] = {}
    for ficha in (
        RespostaAnamnese.objects.filter(
            cliente_id__in=ids, formulario__tipo='ANAMNESE', respondida_em__isnull=False,
        )
        .select_related('formulario')
        .annotate(ordem=Window(RowNumber(), partition_by=[F('cliente_id')],
                               order_by=[F('respondida_em').desc(), F('pk').desc()]),
                  revogada_em=F('cliente__saude_revogada_em'))
        .filter(ordem__lte=limite_fichas)
        .order_by('cliente_id', '-respondida_em', '-pk')
    ):
        fichas.setdefault(ficha.cliente_id, []).append(ficha)
        if ficha.revogada_em is not None:
            revogados[ficha.cliente_id] = ficha.revogada_em

    if perguntas is None and any(
        isinstance(p.respostas_extras, dict) and p.respostas_extras for p in prontuarios.values()
    ):
        perguntas = perguntas_prontuario()
    por_key = {p['key']: p for p in perguntas or []}

    mapa = {}
    for cliente_id in ids:
        alertas = _montar_alertas(prontuarios.get(cliente_id), fichas.get(cliente_id, []), por_key)
        if cliente_id in revogados:
            alertas.insert(0, marcador_revogado(revogados[cliente_id]))
        if alertas:
            mapa[cliente_id] = alertas
    return mapa


def marcador_revogado(em) -> dict:
    """Item da lista de alertas quando a cliente revogou o consentimento de saude (art. 11 I).

    Leva label/valor como os outros itens (toda tela que lista alertas o mostra) e o 'tipo' p/ o
    destaque no partials/_alerta_saude.html. O registro ja feito continua visivel (prazo de guarda).
    """
    from django.utils import timezone

    data = timezone.localtime(em).strftime('%d/%m/%Y') if timezone.is_aware(em) else em.strftime('%d/%m/%Y')
    return {
        'tipo': 'revogado', 'em': em, 'origem': 'consentimento',
        'label': 'Consentimento de saúde', 'valor': f'revogado em {data}',
    }


def _montar_alertas(pront, fichas, perguntas_por_key) -> list[dict]:
    out: list[dict] = []
    vistos: set[tuple[str, str]] = set()

    def add(label, valor, origem):
        chave = (_sem_acento(label), _sem_acento(valor))
        if chave in vistos:
            return
        vistos.add(chave)
        out.append({'label': label, 'valor': valor, 'origem': origem})

    if pront is not None:
        for campo, label in (
            ('alergias', 'Alergias'),
            ('contraindicacoes', 'Contraindicações'),
            ('medicamentos_uso', 'Medicamentos em uso'),
        ):
            valor = _valor_positivo(getattr(pront, campo, None))
            if valor:
                add(label, valor, 'prontuario')
        # Questionario do prontuario (formulario PRONTUARIO): respostas do EAV
        # migrado na 0036 e as gravadas pelo prontuario_salvar. Chave sem
        # pergunta ativa (removida do formulario, sufixo '_x' da migracao)
        # ainda alerta, com label derivado da chave.
        extras = pront.respostas_extras if isinstance(pront.respostas_extras, dict) else {}
        for key, valor in extras.items():
            pergunta = perguntas_por_key.get(key)
            label = str((pergunta or {}).get('label') or rotulo_da_chave(key))
            if not deve_alertar(pergunta, key, label):
                continue
            texto = _valor_positivo(valor)
            if texto:
                add(label, texto, 'prontuario')

    for ficha in fichas:
        schema = ficha.formulario.schema_json if isinstance(ficha.formulario.schema_json, list) else []
        por_key = {c.get('key'): c for c in schema if isinstance(c, dict)}
        respostas = ficha.respostas_json if isinstance(ficha.respostas_json, dict) else {}
        for key, valor in respostas.items():
            pergunta = por_key.get(key)
            label = str((pergunta or {}).get('label') or key)
            if not deve_alertar(pergunta, key, label):
                continue
            texto = _valor_positivo(valor)
            if texto:
                add(label, texto, 'anamnese')
    return out
