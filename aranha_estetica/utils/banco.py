"""Ponte Python <-> logica no Postgres (docs/specs/padrao-sql.md §5.3, §5.6, §5.7).

Contexto da sessao (GUCs ``app.*``) lido por ``fn_registrar_historico`` (trilha
``historico_alteracao``):

    app.usuario_id  pk do usuario autenticado ('' = anonimo/sistema)
    app.ip_origem   IP do cliente HTTP ('' = desconhecido)
    app.origem      web | cron:<job> | celery:<task> | migracao ('' = SQL manual)

``definir_contexto_banco`` usa ``set_config(..., false)`` = nivel de SESSAO: o
Django roda em autocommit (ATOMIC_REQUESTS=False), entao ``SET LOCAL`` morreria
no fim do proprio statement. Com CONN_MAX_AGE a conexao volta no proximo request
da mesma thread — por isso quem define SEMPRE limpa no fim (middleware, cron,
sinais do Celery) e a conexao nova nasce limpa (``connection_created``).
PgBouncer em modo transacao quebraria GUC de sessao: nesse dia, trocar para
ATOMIC_REQUESTS + SET LOCAL.

Procedures (fonte unica no Postgres; fallback Python so no SQLite dos testes):
``usa_logica_pg()`` decide e ``chamar()`` executa o CALL com allowlist.
``settings.FORCAR_LOGICA_PYTHON`` existe SO para o teste de paridade rodar o
fallback Python no Postgres — em producao e sempre False.
"""
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections

# nome -> quantidade de argumentos (IN + INOUT). O nome nunca vem de input.
PROCEDURES = {
    'sp_anonimizar_cliente': 3,
    'sp_aplicar_retencao_lgpd': 8,
    'sp_purgar_dados_expirados': 10,
    'sp_expirar_pacotes': 2,
}

LIMITE_ORIGEM = 60  # historico_alteracao.origem varchar(60)

SQL_CONTEXTO = (
    "SELECT set_config('app.usuario_id', %s, false), "
    "set_config('app.ip_origem', %s, false), "
    "set_config('app.origem', %s, false)"
)


def eh_postgres(alias=DEFAULT_DB_ALIAS) -> bool:
    return connections[alias].vendor == 'postgresql'


def definir_contexto_banco(usuario_id='', ip='', origem='', alias=DEFAULT_DB_ALIAS) -> None:
    """Define (ou limpa, sem argumentos) app.usuario_id/app.ip_origem/app.origem. No-op fora do PG."""
    conexao = connections[alias]
    if conexao.vendor != 'postgresql':
        return
    with conexao.cursor() as cursor:
        cursor.execute(SQL_CONTEXTO, [
            str(usuario_id or ''), str(ip or ''), str(origem or '')[:LIMITE_ORIGEM],
        ])


def limpar_contexto_conexao_nova(sender, connection, **kwargs) -> None:
    """``connection_created``: conexao nova comeca sem autor/IP/origem (defesa em profundidade)."""
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute(SQL_CONTEXTO, ['', '', ''])


def usa_logica_pg(alias=DEFAULT_DB_ALIAS) -> bool:
    """True = chamar a procedure do Postgres; False = fallback Python (SQLite / teste de paridade)."""
    return connections[alias].vendor == 'postgresql' and not getattr(settings, 'FORCAR_LOGICA_PYTHON', False)


def chamar(nome, *args, alias=DEFAULT_DB_ALIAS):
    """CALL <procedure>(...) com allowlist. INOUT vao como None e voltam na tupla (ou None)."""
    if PROCEDURES.get(nome) != len(args):
        raise ValueError(f'procedure desconhecida ou aridade errada: {nome}/{len(args)}')
    with connections[alias].cursor() as cursor:
        cursor.execute(f"CALL {nome}({', '.join(['%s'] * len(args))})", list(args))
        return cursor.fetchone() if cursor.description else None
