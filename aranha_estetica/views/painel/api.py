"""Documentacao da API (OpenAPI 3.1 + Swagger UI), so para a equipe.

A especificacao mora em aranha_estetica/api/openapi.yaml (fonte unica, conferida por
tests/test_openapi.py) e sai crua: o Swagger UI (static/src/js/painel-api.js) le o YAML no
navegador. Fica atras do staff_required (e do 2FA do middleware): descreve webhooks, cron e
rotas internas, nada que a cliente precise ver.
"""
from pathlib import Path

from django.http import HttpResponse
from django.shortcuts import render

from ...decorators import staff_required

ESPECIFICACAO = Path(__file__).resolve().parents[2] / 'api' / 'openapi.yaml'


@staff_required
def documentacao(request):
    """Swagger UI com 'Try it out' so para GET (as escritas mudam dados reais da clinica)."""
    return render(request, 'painel/api.html')


@staff_required
def especificacao(request):
    """O openapi.yaml como esta no repositorio (lido a cada pedido: editar nao exige reiniciar)."""
    resposta = HttpResponse(ESPECIFICACAO.read_bytes(), content_type='application/yaml; charset=utf-8')
    resposta['Content-Disposition'] = 'inline; filename="openapi.yaml"'
    return resposta
