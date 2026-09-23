"""Fichas de anamnese respondidas pela cliente, legiveis p/ a equipe.

RespostaAnamnese guarda so {key: valor}; aqui cada resposta vira uma lista
[(label da pergunta, valor formatado)] usando o schema do formulario (antes a
tela mostrava a chave crua e bool como True/False). Usado no prontuario e na
tela de respostas do painel.
"""
from __future__ import annotations


def formatar_valor(valor) -> str:
    """Valor de resposta como texto: bool -> Sim/Não, lista -> 'a, b'."""
    if valor is True:
        return 'Sim'
    if valor is False:
        return 'Não'
    if valor is None:
        return '—'
    if isinstance(valor, (list, tuple)):
        itens = [str(v).strip() for v in valor if str(v).strip()]
        return ', '.join(itens) or '—'
    texto = str(valor).strip()
    return texto or '—'


def itens_ficha(resposta) -> list[tuple[str, str]]:
    """[(label, valor)] na ordem do schema; chaves fora do schema vao ao fim."""
    schema = resposta.formulario.schema_json
    schema = schema if isinstance(schema, list) else []
    respostas = resposta.respostas_json if isinstance(resposta.respostas_json, dict) else {}
    itens = []
    vistas = set()
    for campo in schema:
        if not isinstance(campo, dict):
            continue
        key = campo.get('key')
        if key not in respostas:
            continue
        vistas.add(key)
        itens.append((str(campo.get('label') or key), formatar_valor(respostas[key])))
    for key, valor in respostas.items():
        if key not in vistas:
            itens.append((str(key), formatar_valor(valor)))
    return itens


def fichas_do_cliente(cliente, limite: int = 10) -> list[dict]:
    """Fichas de ANAMNESE com respostas do cliente, mais recentes primeiro.

    [{'data', 'formulario', 'procedimento', 'atendimento_id', 'itens'}].
    """
    from ..models import RespostaAnamnese

    qs = (
        RespostaAnamnese.objects.filter(cliente=cliente, formulario__tipo='ANAMNESE')
        .select_related('formulario', 'atendimento__procedimento')
        .order_by('-criado_em')
    )
    fichas = []
    for r in qs[: limite * 2]:  # folga p/ convites ainda nao respondidos
        if not r.respostas_json:
            continue
        fichas.append({
            'data': r.respondida_em or r.criado_em,
            'formulario': r.formulario.nome,
            'procedimento': r.atendimento.procedimento.nome if r.atendimento_id else '',
            'atendimento_id': r.atendimento_id,
            'itens': itens_ficha(r),
        })
        if len(fichas) >= limite:
            break
    return fichas
