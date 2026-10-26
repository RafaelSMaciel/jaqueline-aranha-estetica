"""Campos com tipo de banco do padrao SQL (docs/specs/padrao-sql.md §1.2, §7.2).

HashSha256Field — hash de segredo (sha256/HMAC em hex): ``char(64)``, nunca
``varchar``. Sem o ``_like`` (varchar_pattern_ops) que o Django cria para
CharField unico/indexado no Postgres. O CHECK de formato
(``ck_<tabela>_<x>_hash_formato``, regex) e so-PG e vem por migration.
Comparacao com parametro texto funciona (o literal vira ``bpchar``); SQL manual
deve usar sempre o hash inteiro. bulk_create no PG nao usa UNNEST (ver
get_placeholder: o UNNEST truncaria o char(64)).

Modulo CONGELADO (migrations aplicadas importam daqui): mudanca = classe nova.
"""
from django.db import models


class HashSha256Field(models.CharField):
    description = 'Hash SHA-256 em hexadecimal (char(64))'

    def __init__(self, *args, **kwargs):
        kwargs['max_length'] = 64
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop('max_length', None)
        return name, path, args, kwargs

    def db_type(self, connection):
        return 'char(64)'

    def get_placeholder(self, value, compiler, connection):
        # Desliga o bulk_create por UNNEST do Postgres (Django 5.2): ele tira o
        # tamanho do tipo p/ montar o cast do array — char(64)[] vira char[] =
        # char(1)[] e o hash seria TRUNCADO p/ 1 caractere. Com get_placeholder
        # o Django volta ao INSERT ... VALUES (placeholder padrao, sem cast).
        return '%s'
