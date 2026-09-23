"""Alertas de saude do cliente p/ quem executa o procedimento.

Junta o que a equipe preencheu no Prontuario com o que a propria cliente
declarou nas fichas de anamnese (RespostaAnamnese) — antes a alergia
declarada no agendamento nunca chegava ao prontuario/portal/agenda.
"""
from __future__ import annotations

import re

# chave/label de campo de anamnese que indica risco (sem acento, minusculo)
_RISCO_RX = re.compile(
    r'alerg|contraindic|medicament|remedio|gestan|gravid|amament|lactan|'
    r'doenca|cirurg|anticoag|diabet|hipertens|cardi|marca.?passo|queloide|'
    r'isotretinoina|roacutan|herpes|epileps|autoimun|cancer|oncolog',
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


def alertas_saude(cliente, limite_fichas: int = 5) -> list[dict]:
    """[{'label', 'valor', 'origem'}] com alergias/contraindicacoes/etc. do cliente.

    origem: 'prontuario' ou 'anamnese' (ficha respondida pela cliente). Considera
    as `limite_fichas` fichas de ANAMNESE mais recentes respondidas.
    """
    if cliente is None:
        return []
    from ..models import Prontuario, RespostaAnamnese

    out: list[dict] = []
    vistos: set[tuple[str, str]] = set()

    def add(label, valor, origem):
        chave = (_sem_acento(label), _sem_acento(valor))
        if chave in vistos:
            return
        vistos.add(chave)
        out.append({'label': label, 'valor': valor, 'origem': origem})

    pront = Prontuario.objects.filter(cliente=cliente).first()
    if pront is not None:
        for campo, label in (
            ('alergias', 'Alergias'),
            ('contraindicacoes', 'Contraindicações'),
            ('medicamentos_uso', 'Medicamentos em uso'),
        ):
            valor = _valor_positivo(getattr(pront, campo, None))
            if valor:
                add(label, valor, 'prontuario')

    fichas = (
        RespostaAnamnese.objects.filter(
            cliente=cliente, formulario__tipo='ANAMNESE', respondida_em__isnull=False,
        )
        .select_related('formulario')
        .order_by('-respondida_em')[:limite_fichas]
    )
    for ficha in fichas:
        schema = ficha.formulario.schema_json if isinstance(ficha.formulario.schema_json, list) else []
        labels = {c.get('key'): c.get('label') or c.get('key') for c in schema if isinstance(c, dict)}
        respostas = ficha.respostas_json if isinstance(ficha.respostas_json, dict) else {}
        for key, valor in respostas.items():
            label = str(labels.get(key) or key)
            if not _RISCO_RX.search(_sem_acento(f'{key} {label}')):
                continue
            texto = _valor_positivo(valor)
            if texto:
                add(label, texto, 'anamnese')
    return out
