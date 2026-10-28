"""Rotas do app (namespace aranha), uma lista por area — espelha views/<area>/.

Ordem importa so dentro de cada area (ex.: anamnese/obrigado/ antes de anamnese/<token>/).
"""
from . import conta, infra, painel, profissional, publico

app_name = 'aranha'

urlpatterns = [
    *publico.urlpatterns,
    *conta.urlpatterns,
    *painel.urlpatterns,
    *profissional.urlpatterns,
    *infra.urlpatterns,
]
