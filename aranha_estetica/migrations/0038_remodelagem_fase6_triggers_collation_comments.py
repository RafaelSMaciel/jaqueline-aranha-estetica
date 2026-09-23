# Remodelagem v2.1 — Fase 6.3/6.4/6.5 (docs/specs/remodelagem-banco-v2.md)
# Hardening DBA postgres-only (SQLite dos testes nao suporta — vendor-gated):
#
# 6.3 Ledger imutavel: movimento_carteira nunca sofre UPDATE/DELETE no banco
#     (estorno = novo movimento; padrao append-only). movimento_comissao fica
#     FORA por decisao documentada: o fluxo atual usa UPDATE de status
#     (PENDENTE->PAGA/ESTORNADA); migrar p/ ledger puro e refactor futuro.
# 6.4 Collation pt-BR (ICU) nas colunas de nome — ordenacao correta de
#     acentuados (Railway provisiona en_US por default).
# 6.5 COMMENT ON TABLE nas tabelas centrais — catalogo autodocumentado
#     (\\d+ no psql explica o dominio sem abrir o codigo).
#
# Auditoria pre-producao (editada in-place: nenhum PG chegou a aplicar a 0038):
#  - SQL com '%' literal (RAISE do plpgsql) vai com params=None: com o default
#    params=() o psycopg2 faz mogrify, le '%.' como placeholder e a migration
#    quebrava SEMPRE no Postgres (IndexError);
#  - collation DETERMINISTICA (a nao-deterministica nao dava busca sem
#    acento/caixa e faz LIKE/icontains falhar em PG <= 17); tamanho de cada
#    coluna igual ao do model (profissional/procedimento = 100); sem
#    `EXCEPTION WHEN OTHERS THEN NULL`: sem ICU pula com aviso explicito,
#    qualquer outro erro aborta; reverse desfaz collation.
#  - A collation fica FORA do estado do Django (SQLite nao tem pt_br): um
#    AlterField futuro em <tabela>.nome volta p/ a collation padrao — reaplicar
#    com RunSQL `ALTER TABLE <t> ALTER COLUMN nome TYPE varchar(N) COLLATE pt_br`.

from django.db import NotSupportedError, migrations, transaction

COMMENTS = {
    'cliente': 'Cliente da clinica. Soft delete (deletado_em); telefone digits-only e chave natural do booking (uniq_cliente_telefone_ativo).',
    'profissional': 'Profissional de estetica (biomedica). 1:1 opcional com usuario.',
    'procedimento': 'Catalogo de servicos. exige_retorno dispara F-RET (retorno gratuito automatico).',
    'atendimento': 'Agendamento/sessao. FSM: PENDENTE->AGENDADO->CONFIRMADO->REALIZADO|CANCELADO|FALTOU|REAGENDADO. Sobreposicao por profissional bloqueada por excl_atendimento_sobreposicao (EXCLUDE gist).',
    'carteira': 'Saldo de credito do cliente (cashback, vale, refund). Mutacao exige select_for_update; CHECK saldo >= 0.',
    'movimento_carteira': 'Ledger append-only do credito — IMUTAVEL por trigger (estorno = novo movimento; FKs on_delete PROTECT). uniq_cashback_indicacao_por_atendimento garante idempotencia do F-CSB.',
    'movimento_comissao': 'Comissao por atendimento realizado. uniq_comissao_ativa_por_atendimento (status PENDENTE/PAGA) garante idempotencia.',
    'codigo_otp': 'Challenge OTP unico (SMS): codigo em HMAC-SHA256, tentativas com lockout, proposito isola fluxos (AGENDAMENTO/LOGIN/DSAR). Purga apos 24h.',
    'aceite_termo': 'Aceite/assinatura de termo (LGPD ou procedimento — tipo em versao_termo). Evidencia legal: cliente PROTECT, retencao 20 anos.',
    'prontuario': 'Anamnese base permanente. respostas_extras (JSONB) = questionario configuravel (schema em configuracao.prontuario_perguntas).',
    'compra_pacote': 'Compra de pacote pelo cliente (valor pago, expiracao).',
    'consumo_sessao': 'Consumo de 1 sessao do pacote por atendimento (OneToOne atendimento = UNIQUE).',
    'log_auditoria': 'Trilha de auditoria LGPD art. 37 — toda acao sensivel registra autor, tabela, registro e IP.',
}

# (tabela, max_length do model) — collation nao pode alterar o tamanho
COLUNAS_NOME = (('cliente', 150), ('profissional', 100), ('procedimento', 100))


def _scalar(schema_editor, sql):
    with schema_editor.connection.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def aplicar_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    ex = schema_editor.execute

    # ── 6.3 trigger ledger imutavel ── (params=None: o '%' e do plpgsql)
    ex("""
        CREATE OR REPLACE FUNCTION bloquear_mutacao_ledger() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'movimento de ledger e imutavel (%.id=%)', TG_TABLE_NAME, OLD.id;
        END $$ LANGUAGE plpgsql
    """, None)
    ex('DROP TRIGGER IF EXISTS trg_movimento_carteira_imutavel ON movimento_carteira', None)
    ex("""
        CREATE TRIGGER trg_movimento_carteira_imutavel
        BEFORE UPDATE OR DELETE ON movimento_carteira
        FOR EACH ROW EXECUTE FUNCTION bloquear_mutacao_ledger()
    """, None)

    # ── 6.4 collation pt-BR (deterministica) ──
    tem_collation = _scalar(schema_editor, "SELECT to_regcollation('pt_br') IS NOT NULL")
    if not tem_collation:
        # pg_collation lista 'unicode' (provider icu) ate em build SEM ICU:
        # a unica checagem confiavel e tentar (savepoint) e tratar SO o
        # "ICU is not supported in this build" (SQLSTATE 0A000).
        try:
            with transaction.atomic(using=schema_editor.connection.alias):
                ex("CREATE COLLATION IF NOT EXISTS pt_br (provider = icu, locale = 'pt-BR')", None)
            tem_collation = True
        except NotSupportedError:
            print(
                '\n  [0038] AVISO: Postgres sem ICU — collation pt_br NAO aplicada; '
                'nome segue a collation padrao do banco.'
            )
    if tem_collation:
        for tabela, tamanho in COLUNAS_NOME:
            ex(f'ALTER TABLE {tabela} ALTER COLUMN nome TYPE varchar({tamanho}) COLLATE pt_br', None)

    # ── 6.5 comments ──
    for tabela, comentario in COMMENTS.items():
        ex(f"COMMENT ON TABLE {tabela} IS %s", [comentario])


def reverter_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    ex = schema_editor.execute
    ex('DROP TRIGGER IF EXISTS trg_movimento_carteira_imutavel ON movimento_carteira', None)
    ex('DROP FUNCTION IF EXISTS bloquear_mutacao_ledger()', None)
    if _scalar(schema_editor, "SELECT to_regcollation('pt_br') IS NOT NULL"):
        for tabela, tamanho in COLUNAS_NOME:
            ex(f'ALTER TABLE {tabela} ALTER COLUMN nome TYPE varchar({tamanho}) COLLATE "default"', None)
        ex('DROP COLLATION IF EXISTS pt_br', None)
    for tabela in COMMENTS:
        ex(f'COMMENT ON TABLE {tabela} IS NULL', None)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0037_remodelagem_fase6_aceite_termo'),
    ]

    operations = [
        migrations.RunPython(aplicar_pg, reverter_pg),
    ]
