"""Contrato unico das respostas JSON das views (APIs do site, do painel e da infra).

Sucesso:  {"ok": true, ...dados}
Erro:     {"ok": false, "codigo": "<identificador estavel>", "mensagem": "<texto p/ a pessoa>", ...extra}

`codigo` e para o codigo (o front decide o que fazer por ele, nunca pelo texto); `mensagem` e para
a pessoa (pode mudar sem quebrar o front). Webhooks de provedor (Meta, Zenvia) respondem no formato
que o provedor espera e ficam fora deste contrato.
"""
from django.http import JsonResponse

from ..excecoes import RegraDeNegocio

# Sessao so com senha de quem deve o 2FA (middleware e Web Push): o front reconhece pelo codigo
MSG_2FA = 'Confirme o código de verificação em duas etapas.'


def json_ok(status: int = 200, **dados) -> JsonResponse:
    return JsonResponse({'ok': True, **dados}, status=status)


def json_erro(codigo: str, mensagem: str = '', status: int = 400, **extra) -> JsonResponse:
    return JsonResponse({'ok': False, 'codigo': codigo, 'mensagem': mensagem, **extra}, status=status)


def json_recusa(exc: RegraDeNegocio, status: int = 400) -> JsonResponse:
    """Regra de negocio recusada (excecoes.RegraDeNegocio): codigo e mensagem da propria excecao."""
    return json_erro(exc.codigo, exc.mensagem, status=status)
