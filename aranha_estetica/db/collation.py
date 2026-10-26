"""Collation ``pt_br`` no SQLite (spec padrao-sql §3.6.3).

No Postgres a collation ``pt_br`` (ICU, deterministica) e criada pela 0038 e entra no estado
do Django pela 0054 (``CharField(db_collation='pt_br')`` em cliente/profissional/procedimento
``nome``). O SQLite nao a conhece: sem o shim, o ``CREATE TABLE ... COLLATE "pt_br"`` da
migration e qualquer ORDER BY por ``nome`` falhariam ("no such collation sequence").

Comparacao equivalente a da ICU deterministica: primeiro sem acento e sem caixa
(``Álvaro`` antes de ``Bruno``, ``ana`` junto de ``Ana``); empate desfeito pelo texto cru, de
modo que duas strings so sao iguais quando identicas (igualdade continua exata, como no PG).

Registrado em ``AranhaEsteticaConfig.ready()`` via ``connection_created``.
"""
import unicodedata

NOME = 'pt_br'


def _chave(texto):
    decomposto = unicodedata.normalize('NFKD', texto)
    return ''.join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def comparar_pt_br(a, b):
    ka, kb = (_chave(a), a), (_chave(b), b)
    return (ka > kb) - (ka < kb)


def registrar_collation_sqlite(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        connection.connection.create_collation(NOME, comparar_pt_br)
