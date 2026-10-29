"""Validacao das respostas de ficha de anamnese contra o schema do formulario.

Fonte unica p/ o booking (JSON do wizard) e o link publico /anamnese/<token>/
(POST do formulario): descarta chaves fora do schema, confere tipo/opcoes/
tamanho e normaliza bool ('sim'/'nao'/'on'/...) p/ True/False — a mesma
pergunta nao pode ficar gravada ora como 'sim', ora como True.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

# Limites por campo (o do texto curto pode ser ajustado no schema com "max").
MAX_TEXTO = 1000
MAX_TEXTO_LONGO = 4000
MAX_NUMERO = 30

_BOOL_SIM = {'true', 'sim', 's', 'on', '1', 'yes'}
_BOOL_NAO = {'false', 'nao', 'não', 'n', 'off', '0', 'no'}


def _vazio(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, str):
        return not valor.strip()
    if isinstance(valor, (list, tuple)):
        return not any(str(v).strip() for v in valor)
    return False


def _texto(valor, limite, label):
    if isinstance(valor, (dict, list, tuple)):
        return None, f'"{label}": resposta inválida.'
    texto = str(valor).strip()
    if len(texto) > limite:
        return None, f'"{label}": use no máximo {limite} caracteres.'
    return texto, None


def _limpar_campo(campo: dict, valor):
    """(valor_limpo, erro|None) de um campo JA preenchido."""
    tipo = campo.get('tipo', 'text')
    label = campo.get('label') or campo.get('key')

    if tipo == 'bool':
        if isinstance(valor, bool):
            return valor, None
        chave = str(valor).strip().lower() if not isinstance(valor, (dict, list, tuple)) else ''
        if chave in _BOOL_SIM:
            return True, None
        if chave in _BOOL_NAO:
            return False, None
        return None, f'"{label}": responda sim ou não.'

    if tipo == 'checkboxes':
        itens = [valor] if isinstance(valor, str) else valor
        if not isinstance(itens, (list, tuple)) or any(
            isinstance(v, (dict, list, tuple)) for v in itens
        ):
            return None, f'"{label}": resposta inválida.'
        opcoes = {str(o) for o in (campo.get('opcoes') or [])}
        itens = [str(v).strip() for v in itens if str(v).strip()]
        if opcoes and not all(v in opcoes for v in itens):
            return None, f'"{label}": opção(ões) inválida(s).'
        return itens, None

    if tipo in ('select', 'scale'):
        texto, erro = _texto(valor, MAX_TEXTO, label)
        if erro:
            return None, erro
        validas = [str(o) for o in (campo.get('opcoes') or [])]
        if validas and texto not in validas:
            return None, f'"{label}": opção inválida.'
        return texto, None

    if tipo == 'number':
        texto, erro = _texto(valor, MAX_NUMERO, label)
        if erro:
            return None, erro
        try:
            numero = float(texto.replace(',', '.'))
        except ValueError:
            return None, f'"{label}" deve ser um número.'
        if not math.isfinite(numero):
            return None, f'"{label}" deve ser um número.'
        return texto, None

    if tipo == 'date':
        texto, erro = _texto(valor, MAX_NUMERO, label)
        if erro:
            return None, erro
        try:
            date.fromisoformat(texto)
        except ValueError:
            return None, f'"{label}": data inválida.'
        return texto, None

    if tipo == 'email':
        texto, erro = _texto(valor, 254, label)
        if erro:
            return None, erro
        try:
            validate_email(texto)
        except ValidationError:
            return None, f'"{label}" deve ser um e-mail válido.'
        return texto, None

    limite = MAX_TEXTO_LONGO if tipo == 'longtext' else campo.get('max') or MAX_TEXTO
    try:
        limite = min(int(limite), MAX_TEXTO_LONGO)
    except (TypeError, ValueError):
        limite = MAX_TEXTO
    return _texto(valor, limite, label)


def validar_respostas(schema, dados: Mapping) -> tuple[dict, list[str]]:
    """Valida `dados` ({key: valor}) contra o schema_json do formulario.

    Retorna (respostas_limpas, erros). So entram chaves do schema; campo vazio
    (None/''/lista vazia) fica fora do resultado e so gera erro se obrigatorio
    (bool False e resposta valida). bool vira True/False; checkboxes vira lista
    de opcoes validas; demais tipos ficam como texto (strip) dentro do limite.
    """
    limpas: dict = {}
    erros: list[str] = []
    if not isinstance(dados, Mapping):
        dados = {}
    for campo in schema if isinstance(schema, list) else []:
        if not isinstance(campo, dict) or not campo.get('key'):
            continue
        key = campo['key']
        label = campo.get('label') or key
        valor = dados.get(key)
        if _vazio(valor):
            if campo.get('obrigatorio'):
                erros.append(f'"{label}" é obrigatório.')
            continue
        limpo, erro = _limpar_campo(campo, valor)
        if erro:
            erros.append(erro)
            continue
        if campo.get('tipo') == 'checkboxes' and not limpo and campo.get('obrigatorio'):
            erros.append(f'"{label}" é obrigatório.')
            continue
        limpas[key] = limpo
    return limpas, erros


# ─── Convites (link publico /anamnese/<token>/ ou /pesquisa/<token>/) ──────

def convidar(formulario, atendimento) -> str:
    """Caminho do link p/ a cliente responder ``formulario`` sobre o atendimento (60 dias).

    Reaproveita o convite em aberto (RespostaAnamnese sem resposta); cada chamada emite um token
    novo e os anteriores seguem valendo — so o hash fica no banco (link_acesso)."""
    from ..models import RespostaAnamnese
    from . import links

    resposta = RespostaAnamnese.objects.filter(
        formulario=formulario, atendimento=atendimento, respondida_em__isnull=True,
    ).first()
    if resposta is None:
        resposta = RespostaAnamnese.objects.create(
            formulario=formulario, cliente_id=atendimento.cliente_id, atendimento=atendimento,
        )
    return links.emitir_link_formulario(resposta)


# ─── Validade da ficha (D14) ────────────────────────────────────────────

# Ficha respondida vale 12 meses: so a obrigatoria pendente ou vencida e pedida de novo (antes a
# cliente de pacote preenchia a cada sessao no wizard, e o painel aceitava uma de 5 anos atras).
VALIDADE_FICHA_MESES = 12


def valida_desde(em):
    """Resposta dada a partir deste instante ainda vale num atendimento em ``em``."""
    from dateutil.relativedelta import relativedelta  # type: ignore[import-untyped]

    return em - relativedelta(months=VALIDADE_FICHA_MESES)


def fichas_em_dia(cliente, em=None) -> dict:
    """{formulario_id: ultima respondida_em} das fichas ANAMNESE que a cliente respondeu dentro
    da validade (num atendimento em ``em``; padrao agora). Sem cliente: {}."""
    from django.db.models import Max
    from django.utils import timezone

    from ..models import FormularioAnamnese, RespostaAnamnese

    if cliente is None or getattr(cliente, 'pk', cliente) is None:
        return {}
    em = em or timezone.now()
    linhas = (
        RespostaAnamnese.objects.filter(
            cliente_id=getattr(cliente, 'pk', cliente), respondida_em__gte=valida_desde(em),
            formulario__tipo=FormularioAnamnese.TIPO_ANAMNESE,
        ).values('formulario_id').annotate(ultima=Max('respondida_em'))
    )
    return {linha['formulario_id']: linha['ultima'] for linha in linhas}


def fichas_pendentes(atendimento) -> list:
    """Fichas ANAMNESE obrigatorias que valem p/ o procedimento e a cliente nao respondeu dentro
    da validade no dia do atendimento (as mesmas de services.alertas.ids_com_ficha_pendente, p/
    um atendimento; o wizard usa ``fichas_em_dia``)."""
    from ..models import FormularioAnamnese

    em_dia = fichas_em_dia(atendimento.cliente_id, atendimento.data_hora_inicio)
    return list(
        FormularioAnamnese.objects.filter(tipo=FormularioAnamnese.TIPO_ANAMNESE, ativo=True, obrigatorio=True)
        .aplicaveis_a(atendimento.procedimento).exclude(pk__in=em_dia).order_by('pk')
    )


def convite_pesquisa(atendimento) -> str | None:
    """Pesquisa pos-atendimento oferecida no fim do NPS: a PESQUISA ativa que vale p/ o
    procedimento, se a cliente ainda nao respondeu sobre este atendimento. Sem mensagem extra —
    o convite vai no link do NPS que ela ja recebeu."""
    from ..models import FormularioAnamnese, RespostaAnamnese

    formulario = (
        FormularioAnamnese.objects.filter(tipo=FormularioAnamnese.TIPO_PESQUISA, ativo=True)
        .aplicaveis_a(atendimento.procedimento).order_by('pk').first()
    )
    if formulario is None or RespostaAnamnese.objects.filter(
            formulario=formulario, atendimento=atendimento, respondida_em__isnull=False).exists():
        return None
    return convidar(formulario, atendimento)
