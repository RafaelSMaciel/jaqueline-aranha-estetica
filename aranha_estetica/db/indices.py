"""Index com nome no padrao do banco (spec padrao-sql §1.3: ix_<tabela>_<colunas>).

O models.E034 limita Index.name a 30 caracteres por causa do Oracle; o projeto
e Postgres (limite 63 = NAMEDATALEN-1, que o teste de conformidade confere) e
SQLite nos testes. A subclasse so ajusta o limite do check — o DDL e o do
models.Index.
"""
from django.db import models


class Indice(models.Index):
    max_name_length = 63
