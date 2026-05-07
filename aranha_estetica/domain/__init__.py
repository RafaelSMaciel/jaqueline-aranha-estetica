"""Camada de dominio: eventos + value objects + servicos.

Convencao:
- events.py: dataclasses imutaveis representando fatos passados
- event_bus.py: registry sincrono in-process de handlers
- handlers/: subscribers separados por agregado

Nao importar models do Django aqui (mantem dominio puro).
"""
