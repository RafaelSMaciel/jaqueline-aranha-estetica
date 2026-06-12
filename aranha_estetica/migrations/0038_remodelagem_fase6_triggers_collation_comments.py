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

from django.db import migrations

COMMENTS = {
    'cliente': 'Paciente da clinica. Soft delete (deletado_em); telefone digits-only e chave natural do booking (uniq_cliente_telefone_ativo).',
    'profissional': 'Profissional de estetica (biomedica). 1:1 opcional com usuario.',
    'procedimento': 'Catalogo de servicos. exige_retorno dispara F-RET (retorno gratuito automatico).',
    'atendimento': 'Agendamento/sessao. FSM: PENDENTE->AGENDADO->CONFIRMADO->REALIZADO|CANCELADO|FALTOU|REAGENDADO. Sobreposicao por profissional bloqueada por excl_atendimento_sobreposicao (EXCLUDE gist).',
    'carteira': 'Saldo de credito do cliente (cashback, vale, refund). Mutacao exige select_for_update; CHECK saldo >= 0.',
    'movimento_carteira': 'Ledger append-only do credito — IMUTAVEL por trigger (estorno = novo movimento). uniq_cashback_indicacao_por_atendimento garante idempotencia do F-CSB.',
    'movimento_comissao': 'Comissao por atendimento realizado. uniq_comissao_ativa_por_atendimento (status PENDENTE/PAGA) garante idempotencia.',
    'codigo_otp': 'Challenge OTP unico (SMS): codigo hashed sha256, tentativas com lockout, proposito isola fluxos (AGENDAMENTO/LOGIN/DSAR).',
    'aceite_termo': 'Aceite/assinatura de termo (LGPD ou procedimento — tipo em versao_termo). Evidencia legal: cliente PROTECT, retencao 20 anos.',
    'prontuario': 'Anamnese base permanente. respostas_extras (JSONB) = questionario configuravel (schema em configuracao.prontuario_perguntas).',
    'compra_pacote': 'Compra de pacote pelo cliente (valor pago, expiracao).',
    'consumo_sessao': 'Consumo de 1 sessao do pacote por atendimento (uniq_consumo_por_atendimento).',
    'log_auditoria': 'Trilha de auditoria LGPD art. 37 — toda acao sensivel registra autor, tabela, registro e IP.',
}


def aplicar_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    ex = schema_editor.execute

    # ── 6.3 trigger ledger imutavel ──
    ex("""
        CREATE OR REPLACE FUNCTION bloquear_mutacao_ledger() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'movimento de ledger e imutavel (%.id=%)', TG_TABLE_NAME, OLD.id;
        END $$ LANGUAGE plpgsql
    """)
    ex('DROP TRIGGER IF EXISTS trg_movimento_carteira_imutavel ON movimento_carteira')
    ex("""
        CREATE TRIGGER trg_movimento_carteira_imutavel
        BEFORE UPDATE OR DELETE ON movimento_carteira
        FOR EACH ROW EXECUTE FUNCTION bloquear_mutacao_ledger()
    """)

    # ── 6.4 collation pt-BR ──
    ex("""
        DO $$ BEGIN
            CREATE COLLATION IF NOT EXISTS pt_br (provider = icu, locale = 'pt-BR', deterministic = false);
        EXCEPTION WHEN OTHERS THEN NULL; END $$
    """)
    for tabela in ('cliente', 'profissional', 'procedimento'):
        ex(f'''
            DO $$ BEGIN
                ALTER TABLE {tabela} ALTER COLUMN nome TYPE varchar(150) COLLATE pt_br;
            EXCEPTION WHEN OTHERS THEN NULL; END $$
        ''')

    # ── 6.5 comments ──
    for tabela, comentario in COMMENTS.items():
        ex(f"COMMENT ON TABLE {tabela} IS %s", [comentario])


def reverter_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    ex = schema_editor.execute
    ex('DROP TRIGGER IF EXISTS trg_movimento_carteira_imutavel ON movimento_carteira')
    ex('DROP FUNCTION IF EXISTS bloquear_mutacao_ledger()')
    for tabela in COMMENTS:
        ex(f'COMMENT ON TABLE {tabela} IS NULL')


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0037_remodelagem_fase6_aceite_termo'),
    ]

    operations = [
        migrations.RunPython(aplicar_pg, reverter_pg),
    ]
