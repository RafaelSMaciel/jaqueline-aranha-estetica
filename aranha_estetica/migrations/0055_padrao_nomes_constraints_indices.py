# Padrao SQL — fase de nomes N2: constraints e indices (docs/specs/padrao-sql.md §1.3, §2, §2.11, §3.3, §3.5, §3.6, §6)
#
# Ordem (§6, N2):
#   1. pre-checagem (aborta listando os ids, padrao 0035/0043/0051/0053): todo CHECK/UNIQUE novo e
#      os VALIDATE dos CHECK jsonb NOT VALID da 0043 — nada e aplicado se houver linha violando
#   2. RenomearIndice x15 (indices do Meta: mesmo DDL, nome ix_ e classe Indice no estado)
#   3. RenomearConstraint x39 (§3.3 RENAME: ALTER TABLE ... RENAME CONSTRAINT / ALTER INDEX ...
#      RENAME no PG) + PG: excl_atendimento_sobreposicao -> ex_atendimento_sobreposicao e os 3 CHECK
#      jsonb da 0043 renomeados + VALIDATE
#   4. unique/db_index de campo e unique_together -> Meta (uq_/ix_ com nome do padrao; o Django
#      solta o objeto antigo por introspecao — e o _like das tabelas que nao foram renomeadas)
#   5. indices redundantes saem (§2.11); RECRIA: uq_cliente_{telefone,cpf}_ativo (predicado so
#      deletado_em IS NULL: vazio e NULL desde a 0054), uq_cliente_email_ativo (UPPER, funde
#      uniq_cliente_email_ativo LOWER + idx_cliente_email_upper), ck_codigo_otp_qtd_tentativas_0_10
#      (BETWEEN 0 AND 10: absorve o CHECK automatico do PositiveSmallIntegerField)
#   6. indices novos (compostos/parciais das queries reais, §2.11)
#   7. CHECKs novos portaveis (Meta; existem no SQLite tambem)
#   8. PG: CHECK regex de cliente recriados sem a excecao '' (ck_cliente_{telefone,cpf}_formato),
#      CHECK jsonb/regex novos (log_auditoria.detalhes, prontuario_versao.dados, conteudo_hash,
#      codigo_hash) e DROP do gin_prontuario_extras (sem query de contencao)
#   9. PG: PadronizarNomes() em todas as tabelas do app — pk_<t>, fk_<t>_<col> recriadas com o
#      ON DELETE do on_delete (§1.3), uq_ dos OneToOne, sequencias <t>_id_seq, DROP dos _like
#  10. COMMENT de tabela (db_table_comment) e de coluna (db_comment) — as 35 tabelas do app
#      (historico_alteracao nasce na 0056 ja comentada); substituem os COMMENT da 0038
#
# Pre-checagem p/ o runbook (mesma regra; nomes de coluna ja os da 0054):
#   SELECT id FROM atendimento WHERE valor_cobrado < 0 OR valor_original < 0;
#   SELECT id FROM cliente WHERE qtd_faltas_consecutivas < 0 OR email = ''
#       OR telefone !~ '^[0-9]{10,11}$' OR cpf !~ '^[0-9]{11}$';
#   SELECT telefone, array_agg(id) FROM cliente WHERE deletado_em IS NULL AND telefone IS NOT NULL
#       GROUP BY 1 HAVING count(*) > 1;   -- idem cpf e upper(email::text)
#   SELECT id FROM notificacao WHERE tipo NOT IN ('LEMBRETE','LEMBRETE_2H','CONFIRMACAO','CANCELAMENTO',
#       'NPS','PESQUISA','APROVACAO','TERMO') OR canal NOT IN ('WHATSAPP','SMS','EMAIL')
#       OR status NOT IN ('PENDENTE','ENVIADO','FALHOU') OR resposta NOT IN ('CONFIRMOU','CANCELOU');
#   SELECT id FROM lista_espera WHERE email_contato = '';
#   SELECT id FROM avaliacao_nps WHERE eh_publicacao_aprovada AND NOT eh_publicacao_autorizada;
#   SELECT id FROM codigo_otp WHERE canal NOT IN ('EMAIL','SMS') OR qtd_tentativas NOT BETWEEN 0 AND 10
#       OR proposito NOT IN ('AGENDAMENTO','LOGIN_CLIENTE','DSAR') OR codigo_hash !~ '^[0-9a-f]{64}$';
#   SELECT id FROM procedimento WHERE retorno_minimo_dias < 0 OR retorno_maximo_dias < 0
#       OR duracao_retorno_minutos < 0;
#   SELECT id FROM movimento_carteira WHERE tipo NOT IN ('CREDITO','DEBITO') OR valor <= 0
#       OR valor_saldo_resultante < 0 OR origem NOT IN ('VALE_PRESENTE','CANCELAMENTO','AJUSTE_MANUAL',
#       'USO_AGENDAMENTO','CASHBACK_INDICACAO','CASHBACK_ESTORNO','OUTRO');
#   SELECT id FROM regra_comissao WHERE valor_fixo < 0;
#   SELECT id FROM log_auditoria WHERE jsonb_typeof(detalhes) <> 'object';
#   SELECT id FROM prontuario_versao WHERE jsonb_typeof(dados) <> 'object';
#   SELECT id FROM aceite_termo WHERE conteudo_hash !~ '^[0-9a-f]{64}$';
#   SELECT id FROM prontuario WHERE jsonb_typeof(respostas) <> 'object';            -- VALIDATE 0043
#   SELECT id FROM resposta_formulario WHERE jsonb_typeof(respostas) <> 'object';   -- VALIDATE 0043
#   SELECT id FROM formulario WHERE jsonb_typeof(perguntas) <> 'array';             -- VALIDATE 0043
#
# Reverso (dev/testes; rollback de producao = restaurar o dump): renames desfeitos, indices/constraints
# antigos recriados, CHECKs PG da 0034/0036 de volta, COMMENT da 0038 restaurado. O PadronizarNomes nao
# desfaz (PK/FK/sequencia sao achados por introspecao — §3.6.1) e o VALIDATE fica (constraint valida).

import django.core.validators
import django.db.models.deletion
import django.db.models.functions.text
from django.db import migrations, models
from django.db.models import Count, F, Q
from django.db.models.functions import Upper
from django.conf import settings

import aranha_estetica.db.campos
import aranha_estetica.db.indices
from aranha_estetica.db.operacoes import (
    ComentarColuna, PadronizarNomes, RenomearConstraint, RenomearIndice, SoltarIndiceDeCampo,
)

APP = 'aranha_estetica'

# 0038 (COMMENT ON TABLE fora do estado): restaurados no reverso
COMENTARIOS_0038 = {
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

# ─── 3. PG: objetos fora do estado renomeados (0035/0043) ──────────────────
RENOMES_PG = (
    # (tabela, antigo, novo, validar)
    ('atendimento', 'excl_atendimento_sobreposicao', 'ex_atendimento_sobreposicao', False),
    ('prontuario', 'chk_prontuario_extras_objeto', 'ck_prontuario_respostas_objeto', True),
    ('resposta_formulario', 'chk_resposta_anamnese_objeto', 'ck_resposta_formulario_respostas_objeto', True),
    ('formulario', 'chk_formulario_schema_lista', 'ck_formulario_perguntas_lista', True),
)

# ─── 8. PG: CHECKs regex/jsonb (o SQLite nao tem) ──────────────────────────
SQL_PG = [
    'ALTER TABLE cliente DROP CONSTRAINT IF EXISTS chk_cliente_telefone_digits',
    'ALTER TABLE cliente DROP CONSTRAINT IF EXISTS chk_cliente_cpf_digits',
    "ALTER TABLE cliente ADD CONSTRAINT ck_cliente_telefone_formato "
    "CHECK (telefone IS NULL OR telefone ~ '^[0-9]{10,11}$')",
    "ALTER TABLE cliente ADD CONSTRAINT ck_cliente_cpf_formato CHECK (cpf IS NULL OR cpf ~ '^[0-9]{11}$')",
    "ALTER TABLE log_auditoria ADD CONSTRAINT ck_log_auditoria_detalhes_objeto "
    "CHECK (detalhes IS NULL OR jsonb_typeof(detalhes) = 'object')",
    "ALTER TABLE prontuario_versao ADD CONSTRAINT ck_prontuario_versao_dados_objeto "
    "CHECK (jsonb_typeof(dados) = 'object')",
    "ALTER TABLE aceite_termo ADD CONSTRAINT ck_aceite_termo_conteudo_hash_formato "
    "CHECK (conteudo_hash IS NULL OR conteudo_hash ~ '^[0-9a-f]{64}$')",
    "ALTER TABLE codigo_otp ADD CONSTRAINT ck_codigo_otp_codigo_hash_formato "
    "CHECK (codigo_hash ~ '^[0-9a-f]{64}$')",
    # sem query de contencao (@>) no codigo (§2.11): recriar quando houver busca por resposta
    'DROP INDEX IF EXISTS gin_prontuario_extras',
]
SQL_PG_REVERSO = [
    'CREATE INDEX IF NOT EXISTS gin_prontuario_extras ON prontuario USING gin (respostas)',
    'ALTER TABLE codigo_otp DROP CONSTRAINT IF EXISTS ck_codigo_otp_codigo_hash_formato',
    'ALTER TABLE aceite_termo DROP CONSTRAINT IF EXISTS ck_aceite_termo_conteudo_hash_formato',
    'ALTER TABLE prontuario_versao DROP CONSTRAINT IF EXISTS ck_prontuario_versao_dados_objeto',
    'ALTER TABLE log_auditoria DROP CONSTRAINT IF EXISTS ck_log_auditoria_detalhes_objeto',
    'ALTER TABLE cliente DROP CONSTRAINT IF EXISTS ck_cliente_cpf_formato',
    'ALTER TABLE cliente DROP CONSTRAINT IF EXISTS ck_cliente_telefone_formato',
    # = 0034
    "ALTER TABLE cliente ADD CONSTRAINT chk_cliente_telefone_digits "
    "CHECK (telefone IS NULL OR telefone = '' OR telefone ~ '^[0-9]{10,11}$')",
    "ALTER TABLE cliente ADD CONSTRAINT chk_cliente_cpf_digits "
    "CHECK (cpf IS NULL OR cpf = '' OR cpf ~ '^[0-9]{11}$')",
]

# ─── 1. pre-checagem ────────────────────────────────────────────────────────
NOTIFICACAO_TIPOS = ['LEMBRETE', 'LEMBRETE_2H', 'CONFIRMACAO', 'CANCELAMENTO', 'NPS', 'PESQUISA', 'APROVACAO', 'TERMO']
MOVIMENTO_ORIGENS = ['VALE_PRESENTE', 'CANCELAMENTO', 'AJUSTE_MANUAL', 'USO_AGENDAMENTO', 'CASHBACK_INDICACAO',
                     'CASHBACK_ESTORNO', 'OUTRO']
# (tabela, regra, SQL que devolve os ids violadores) — so PG
PRE_CHECAGEM_PG = (
    ('cliente', 'ck_cliente_telefone_formato', "SELECT id FROM cliente WHERE telefone !~ '^[0-9]{10,11}$'"),
    ('cliente', 'ck_cliente_cpf_formato', "SELECT id FROM cliente WHERE cpf !~ '^[0-9]{11}$'"),
    ('log_auditoria', 'ck_log_auditoria_detalhes_objeto',
     "SELECT id FROM log_auditoria WHERE jsonb_typeof(detalhes) <> 'object'"),
    ('prontuario_versao', 'ck_prontuario_versao_dados_objeto',
     "SELECT id FROM prontuario_versao WHERE jsonb_typeof(dados) <> 'object'"),
    ('aceite_termo', 'ck_aceite_termo_conteudo_hash_formato',
     "SELECT id FROM aceite_termo WHERE conteudo_hash !~ '^[0-9a-f]{64}$'"),
    ('codigo_otp', 'ck_codigo_otp_codigo_hash_formato',
     "SELECT id FROM codigo_otp WHERE codigo_hash !~ '^[0-9a-f]{64}$'"),
    ('prontuario', 'ck_prontuario_respostas_objeto (VALIDATE)',
     "SELECT id FROM prontuario WHERE jsonb_typeof(respostas) <> 'object'"),
    ('resposta_formulario', 'ck_resposta_formulario_respostas_objeto (VALIDATE)',
     "SELECT id FROM resposta_formulario WHERE jsonb_typeof(respostas) <> 'object'"),
    ('formulario', 'ck_formulario_perguntas_lista (VALIDATE)',
     "SELECT id FROM formulario WHERE jsonb_typeof(perguntas) <> 'array'"),
)


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def _ids(qs):
    return list(qs.order_by('pk').values_list('pk', flat=True)[:50])


def pre_checar(apps, schema_editor):
    """Aborta (nada aplicado) se alguma linha viola CHECK/UNIQUE novo ou o VALIDATE da 0043."""
    def m(nome):
        return apps.get_model(APP, nome)._base_manager

    regras = [
        ('ck_atendimento_valores_nao_negativos', m('Atendimento').filter(Q(valor_cobrado__lt=0) | Q(valor_original__lt=0))),
        ('ck_cliente_qtd_faltas_nao_negativa', m('Cliente').filter(faltas_consecutivas__lt=0)),
        ('ck_cliente_email_nao_vazio', m('Cliente').filter(email='')),
        ('ck_notificacao_tipo', m('Notificacao').exclude(tipo__in=NOTIFICACAO_TIPOS)),
        ('ck_notificacao_canal', m('Notificacao').exclude(canal__in=['WHATSAPP', 'SMS', 'EMAIL'])),
        ('ck_notificacao_status', m('Notificacao').exclude(status__in=['PENDENTE', 'ENVIADO', 'FALHOU'])),
        ('ck_notificacao_resposta',
         m('Notificacao').filter(resposta__isnull=False).exclude(resposta__in=['CONFIRMOU', 'CANCELOU'])),
        ('ck_lista_espera_email_contato_nao_vazio', m('ListaEspera').filter(email_contato='')),
        ('ck_avaliacao_nps_aprovacao_exige_autorizacao',
         m('AvaliacaoNPS').filter(aprovado_publicacao=True, autoriza_publicacao=False)),
        ('ck_codigo_otp_canal', m('CodigoOtp').exclude(canal__in=['EMAIL', 'SMS'])),
        ('ck_codigo_otp_proposito', m('CodigoOtp').exclude(proposito__in=['AGENDAMENTO', 'LOGIN_CLIENTE', 'DSAR'])),
        ('ck_codigo_otp_qtd_tentativas_0_10', m('CodigoOtp').filter(Q(tentativas__lt=0) | Q(tentativas__gt=10))),
        ('ck_procedimento_*_nao_negativo', m('Procedimento').filter(
            Q(retorno_minimo_dias__lt=0) | Q(retorno_maximo_dias__lt=0) | Q(duracao_retorno_minutos__lt=0))),
        ('ck_movimento_carteira_tipo', m('MovimentoCarteira').exclude(tipo__in=['CREDITO', 'DEBITO'])),
        ('ck_movimento_carteira_origem', m('MovimentoCarteira').exclude(origem__in=MOVIMENTO_ORIGENS)),
        ('ck_movimento_carteira_valor_positivo', m('MovimentoCarteira').filter(valor__lte=0)),
        ('ck_movimento_carteira_valor_saldo_resultante_nao_negativo',
         m('MovimentoCarteira').filter(saldo_resultante__lt=0)),
        ('ck_regra_comissao_valor_fixo_nao_negativo', m('RegraComissao').filter(valor__lt=0)),
    ]
    problemas = [f'{regra}: ids {ids}' for regra, qs in regras if (ids := _ids(qs))]
    # UNIQUE parciais recriados (predicado novo / UPPER no lugar de LOWER)
    ativos = m('Cliente').filter(deletado_em__isnull=True)
    for regra, campo, expr in (('uq_cliente_telefone_ativo', 'telefone', F('telefone')),
                               ('uq_cliente_cpf_ativo', 'cpf', F('cpf')),
                               ('uq_cliente_email_ativo', 'email', Upper('email'))):
        base = ativos.filter(**{f'{campo}__isnull': False}).annotate(valor_unico=expr)
        repetidos = (base.order_by().values('valor_unico').annotate(n=Count('pk')).filter(n__gt=1)
                     .values_list('valor_unico', flat=True))
        for valor in repetidos:
            problemas.append(f'{regra}: ids {_ids(base.filter(valor_unico=valor))} repetem o mesmo valor')
    if _pg(schema_editor):
        with schema_editor.connection.cursor() as cursor:
            for _tabela, regra, sql in PRE_CHECAGEM_PG:
                cursor.execute(sql + ' ORDER BY id LIMIT 50')
                if ids := [r[0] for r in cursor.fetchall()]:
                    problemas.append(f'{regra}: ids {ids}')
    if problemas:
        raise RuntimeError(
            '0055 (padrao de nomes: constraints e indices) abortada — corrija os dados e rode de novo '
            '(pre-checagem SQL no topo da migration e no runbook, docs/specs/padrao-sql.md §6): '
            + ' | '.join(problemas)
        )


# ─── 3. PG: renomes fora do estado ─────────────────────────────────────────
def _existe_constraint(schema_editor, tabela, nome):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SELECT 1 FROM pg_constraint WHERE conrelid = %s::regclass AND conname = %s', [tabela, nome])
        return cursor.fetchone() is not None


def renomear_objetos_pg(apps, schema_editor):
    if not _pg(schema_editor):
        return
    for tabela, antigo, novo, validar in RENOMES_PG:
        if _existe_constraint(schema_editor, tabela, antigo):
            schema_editor.execute(f'ALTER TABLE {tabela} RENAME CONSTRAINT {antigo} TO {novo}', None)
        if validar:
            schema_editor.execute(f'ALTER TABLE {tabela} VALIDATE CONSTRAINT {novo}', None)


def desfazer_renomes_pg(apps, schema_editor):
    if not _pg(schema_editor):
        return
    for tabela, antigo, novo, _validar in RENOMES_PG:
        if _existe_constraint(schema_editor, tabela, novo):
            schema_editor.execute(f'ALTER TABLE {tabela} RENAME CONSTRAINT {novo} TO {antigo}', None)


def aplicar_checks_pg(apps, schema_editor):
    if _pg(schema_editor):
        for sql in SQL_PG:
            schema_editor.execute(sql, None)


def remover_checks_pg(apps, schema_editor):
    if _pg(schema_editor):
        for sql in SQL_PG_REVERSO:
            schema_editor.execute(sql, None)


def restaurar_comentarios_0038(apps, schema_editor):
    """Reverso: o AlterModelTableComment reverso apaga o COMMENT; volta o texto da 0038."""
    if _pg(schema_editor):
        for tabela, comentario in COMENTARIOS_0038.items():
            schema_editor.execute(f'COMMENT ON TABLE {tabela} IS %s', [comentario])


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0054_padrao_nomes_tabelas_colunas'),
    ]

    operations = [
        # ── 1. pre-checagem: aborta antes de qualquer DDL ──
        migrations.RunPython(pre_checar, migrations.RunPython.noop),
        # ── 2. indices do Meta renomeados (§3.3 RENAME) ──
        RenomearIndice(
            model_name='anotacaosessao',
            new_name='ix_anotacao_sessao_atendimento_criado_em',
            old_name='idx_anot_atn_criado',
        ),
        RenomearIndice(
            model_name='atendimento',
            new_name='ix_atendimento_profissional_data_hora_inicio',
            old_name='idx_atend_prof_data',
        ),
        RenomearIndice(
            model_name='atendimento',
            new_name='ix_atendimento_data_hora_inicio',
            old_name='idx_atendimento_data',
        ),
        RenomearIndice(
            model_name='cliente',
            new_name='ix_cliente_nome',
            old_name='idx_cliente_nome',
        ),
        RenomearIndice(
            model_name='comprapacote',
            new_name='ix_compra_pacote_cliente_status',
            old_name='idx_pacote_cli_status',
        ),
        RenomearIndice(
            model_name='consumosessao',
            new_name='ix_consumo_sessao_compra_pacote',
            old_name='idx_sessao_pct_cli',
        ),
        RenomearIndice(
            model_name='listaespera',
            new_name='ix_lista_espera_cliente',
            old_name='idx_espera_cliente',
        ),
        RenomearIndice(
            model_name='logauditoria',
            new_name='ix_log_auditoria_criado_em',
            old_name='idx_auditoria_criado',
        ),
        RenomearIndice(
            model_name='movimentocarteira',
            new_name='ix_movimento_carteira_carteira_criado_em',
            old_name='idx_movcart_cart',
        ),
        RenomearIndice(
            model_name='movimentocomissao',
            new_name='ix_movimento_comissao_criado_em',
            old_name='idx_comissao_criado',
        ),
        RenomearIndice(
            model_name='notificacao',
            new_name='ix_notificacao_atendimento_tipo',
            old_name='idx_notif_atend_tipo',
        ),
        RenomearIndice(
            model_name='notificacao',
            new_name='ix_notificacao_criado_em',
            old_name='idx_notificacao_criado',
        ),
        RenomearIndice(
            model_name='prontuarioversao',
            new_name='ix_prontuario_versao_prontuario_criado_em',
            old_name='idx_pront_versao',
        ),
        RenomearIndice(
            model_name='respostaanamnese',
            new_name='ix_resposta_formulario_atendimento',
            old_name='idx_anamnese_resp_atend',
        ),
        RenomearIndice(
            model_name='respostaanamnese',
            new_name='ix_resposta_formulario_cliente_criado_em',
            old_name='idx_anamnese_resp_cli',
        ),
        # ── 3. constraints do Meta renomeadas (§3.3 RENAME, 39) ──
        RenomearConstraint(
            model_name='aceitetermo',
            old_name='uniq_aceite_cliente_versao',
            new_name='uq_aceite_termo_cliente_versao',
        ),
        RenomearConstraint(
            model_name='atendimento',
            old_name='chk_atendimento_fim_apos_inicio',
            new_name='ck_atendimento_fim_apos_inicio',
        ),
        RenomearConstraint(
            model_name='atendimento',
            old_name='chk_atendimento_status_v2',
            new_name='ck_atendimento_status',
        ),
        RenomearConstraint(
            model_name='atendimento',
            old_name='chk_retorno_tem_origem',
            new_name='ck_atendimento_retorno_tem_origem',
        ),
        RenomearConstraint(
            model_name='atendimento',
            old_name='chk_retorno_valor_zero',
            new_name='ck_atendimento_retorno_valor_zero',
        ),
        RenomearConstraint(
            model_name='atendimento',
            old_name='uniq_retorno_por_origem',
            new_name='uq_atendimento_retorno_por_origem',
        ),
        RenomearConstraint(
            model_name='avaliacaonps',
            old_name='chk_avaliacao_nps_nota',
            new_name='ck_avaliacao_nps_nota_0_10',
        ),
        RenomearConstraint(
            model_name='carteira',
            old_name='chk_carteira_saldo_nao_negativo',
            new_name='ck_carteira_valor_saldo_nao_negativo',
        ),
        RenomearConstraint(
            model_name='cliente',
            old_name='chk_cliente_nao_indica_si_mesmo',
            new_name='ck_cliente_nao_indica_a_si',
        ),
        RenomearConstraint(
            model_name='comprapacote',
            old_name='chk_compra_pacote_valor_pago',
            new_name='ck_compra_pacote_valor_pago_nao_negativo',
        ),
        RenomearConstraint(
            model_name='comprapacote',
            old_name='chk_compra_pacote_valor_reembolsado',
            new_name='ck_compra_pacote_valor_reembolsado',
        ),
        RenomearConstraint(
            model_name='comprapacote',
            old_name='chk_pacote_cliente_status',
            new_name='ck_compra_pacote_status',
        ),
        RenomearConstraint(
            model_name='itempacote',
            old_name='chk_item_pacote_qtd_positiva',
            new_name='ck_pacote_item_qtd_sessoes_positiva',
        ),
        RenomearConstraint(
            model_name='listaespera',
            old_name='chk_lista_espera_turno',
            new_name='ck_lista_espera_turno',
        ),
        RenomearConstraint(
            model_name='listaespera',
            old_name='uniq_espera_ativa',
            new_name='uq_lista_espera_pendente',
        ),
        RenomearConstraint(
            model_name='movimentocarteira',
            old_name='uniq_cashback_indicacao_por_atendimento',
            new_name='uq_movimento_carteira_cashback_por_atendimento',
        ),
        RenomearConstraint(
            model_name='pacote',
            old_name='chk_pacote_preco_positivo',
            new_name='ck_pacote_valor_total_nao_negativo',
        ),
        RenomearConstraint(
            model_name='pacote',
            old_name='chk_pacote_validade_positiva',
            new_name='ck_pacote_validade_meses_positiva',
        ),
        RenomearConstraint(
            model_name='preco',
            old_name='chk_preco_valor_positivo',
            new_name='ck_preco_valor_nao_negativo',
        ),
        RenomearConstraint(
            model_name='preco',
            old_name='uniq_preco_base_vigencia',
            new_name='uq_preco_base_vigencia',
        ),
        RenomearConstraint(
            model_name='preco',
            old_name='uniq_preco_vigencia',
            new_name='uq_preco_vigencia',
        ),
        RenomearConstraint(
            model_name='procedimento',
            old_name='chk_procedimento_buffer_range',
            new_name='ck_procedimento_intervalo_0_120',
        ),
        RenomearConstraint(
            model_name='procedimento',
            old_name='chk_procedimento_categoria',
            new_name='ck_procedimento_categoria',
        ),
        RenomearConstraint(
            model_name='procedimento',
            old_name='chk_procedimento_duracao_positiva',
            new_name='ck_procedimento_duracao_positiva',
        ),
        RenomearConstraint(
            model_name='procedimento',
            old_name='chk_procedimento_modalidade',
            new_name='ck_procedimento_modalidade',
        ),
        RenomearConstraint(
            model_name='procedimento',
            old_name='chk_procedimento_prazo_retorno_coerente',
            new_name='ck_procedimento_retorno_coerente',
        ),
        RenomearConstraint(
            model_name='profissional',
            old_name='chk_profissional_max_advance_range',
            new_name='ck_profissional_antecedencia_maxima',
        ),
        RenomearConstraint(
            model_name='profissional',
            old_name='chk_profissional_min_notice_range',
            new_name='ck_profissional_antecedencia_minima',
        ),
        RenomearConstraint(
            model_name='promocao',
            old_name='chk_promocao_data_fim_valida',
            new_name='ck_promocao_periodo',
        ),
        RenomearConstraint(
            model_name='promocao',
            old_name='chk_promocao_desconto_0_100',
            new_name='ck_promocao_percentual_desconto_0_100',
        ),
        RenomearConstraint(
            model_name='promocao',
            old_name='chk_promocao_desconto_xor_preco',
            new_name='ck_promocao_desconto_xor_valor',
        ),
        RenomearConstraint(
            model_name='promocao',
            old_name='chk_promocao_geral_so_percentual',
            new_name='ck_promocao_geral_so_percentual',
        ),
        RenomearConstraint(
            model_name='promocao',
            old_name='chk_promocao_preco_positivo',
            new_name='ck_promocao_valor_promocional_nao_negativo',
        ),
        RenomearConstraint(
            model_name='regracomissao',
            old_name='chk_regra_comissao_percentual_0_100',
            new_name='ck_regra_comissao_percentual_0_100',
        ),
        RenomearConstraint(
            model_name='regracomissao',
            old_name='chk_regra_comissao_percentual_xor_fixo',
            new_name='ck_regra_comissao_percentual_xor_valor_fixo',
        ),
        RenomearConstraint(
            model_name='usuario',
            old_name='chk_usuario_papel',
            new_name='ck_usuario_papel',
        ),
        RenomearConstraint(
            model_name='versaotermo',
            old_name='chk_versao_termo_tipo',
            new_name='ck_versao_termo_tipo',
        ),
        RenomearConstraint(
            model_name='versaotermo',
            old_name='uniq_termo_ativo_global',
            new_name='uq_versao_termo_ativa_global',
        ),
        RenomearConstraint(
            model_name='versaotermo',
            old_name='uniq_termo_ativo_por_escopo',
            new_name='uq_versao_termo_ativa_por_procedimento',
        ),
        migrations.RunPython(renomear_objetos_pg, desfazer_renomes_pg),
        # ── 4. unique/unique_together de campo -> Meta (uq_ com nome do padrao) ──
        migrations.AlterUniqueTogether(
            name='habilitacao',
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name='habilitacao',
            constraint=models.UniqueConstraint(fields=('profissional', 'procedimento'), name='uq_habilitacao_profissional_procedimento'),
        ),
        migrations.AlterUniqueTogether(
            name='itempacote',
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name='itempacote',
            constraint=models.UniqueConstraint(fields=('pacote', 'procedimento'), name='uq_pacote_item_pacote_procedimento'),
        ),
        migrations.AlterField(
            model_name='assinaturapush',
            name='endpoint',
            field=models.URLField(max_length=600),
        ),
        migrations.AddConstraint(
            model_name='assinaturapush',
            constraint=models.UniqueConstraint(fields=('endpoint',), name='uq_assinatura_push_endpoint'),
        ),
        migrations.AlterField(
            model_name='cliente',
            name='codigo_indicacao',
            field=models.CharField(blank=True, db_comment='Codigo curto compartilhavel (hex maiusculo) gerado no save.', help_text='Codigo curto compartilhavel (ex: A1B2C3D4) gerado automaticamente.', max_length=12, null=True),
        ),
        migrations.AddConstraint(
            model_name='cliente',
            constraint=models.UniqueConstraint(fields=('codigo_indicacao',), name='uq_cliente_codigo_indicacao'),
        ),
        migrations.AlterField(
            model_name='configuracao',
            name='chave',
            field=models.CharField(max_length=100),
        ),
        migrations.AddConstraint(
            model_name='configuracao',
            constraint=models.UniqueConstraint(fields=('chave',), name='uq_configuracao_chave'),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='slug',
            field=models.SlugField(blank=True, db_index=False, max_length=140, null=True),
        ),
        migrations.AddConstraint(
            model_name='procedimento',
            constraint=models.UniqueConstraint(fields=('slug',), name='uq_procedimento_slug'),
        ),
        migrations.AlterField(
            model_name='profissional',
            name='slug',
            field=models.SlugField(blank=True, db_index=False, max_length=140, null=True),
        ),
        migrations.AddConstraint(
            model_name='profissional',
            constraint=models.UniqueConstraint(fields=('slug',), name='uq_profissional_slug'),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='email',
            field=models.EmailField(max_length=100),
        ),
        migrations.AddConstraint(
            model_name='usuario',
            constraint=models.UniqueConstraint(fields=('email',), name='uq_usuario_email'),
        ),
        # ── 4. db_index de campo / indice automatico de FK -> fora (ix_ explicito no Meta) ──
        SoltarIndiceDeCampo(
            model_name='aceitetermo',
            name='atendimento',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to='aranha_estetica.atendimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='aceitetermo',
            name='cliente',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='aceites', to='aranha_estetica.cliente'),
        ),
        SoltarIndiceDeCampo(
            model_name='aceitetermo',
            name='versao_termo',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.RESTRICT, to='aranha_estetica.versaotermo'),
        ),
        SoltarIndiceDeCampo(
            model_name='anotacaosessao',
            name='atendimento',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='anotacoes', to='aranha_estetica.atendimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='anotacaosessao',
            name='autor',
            field=models.ForeignKey(blank=True, db_column='usuario_autor_id', db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL),
        ),
        SoltarIndiceDeCampo(
            model_name='assinaturapush',
            name='user',
            field=models.ForeignKey(db_column='usuario_id', db_index=False, on_delete=django.db.models.deletion.CASCADE, related_name='push_subscriptions', to=settings.AUTH_USER_MODEL),
        ),
        SoltarIndiceDeCampo(
            model_name='atendimento',
            name='atendimento_origem',
            field=models.ForeignKey(blank=True, db_comment='Sessao principal quando este e o retorno gratuito (eh_retorno).', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='retornos', to='aranha_estetica.atendimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='atendimento',
            name='cliente',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.RESTRICT, to='aranha_estetica.cliente'),
        ),
        SoltarIndiceDeCampo(
            model_name='atendimento',
            name='eh_retorno',
            field=models.BooleanField(db_default=False, default=False),
        ),
        SoltarIndiceDeCampo(
            model_name='atendimento',
            name='procedimento',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.RESTRICT, to='aranha_estetica.procedimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='atendimento',
            name='profissional',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.RESTRICT, to='aranha_estetica.profissional'),
        ),
        SoltarIndiceDeCampo(
            model_name='atendimento',
            name='promocao',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to='aranha_estetica.promocao'),
        ),
        SoltarIndiceDeCampo(
            model_name='atendimento',
            name='reagendado_de',
            field=models.ForeignKey(blank=True, db_column='atendimento_reagendado_id', db_comment='Atendimento anterior (status REAGENDADO) que este substitui.', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reagendamentos', to='aranha_estetica.atendimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='cliente',
            name='deletado_em',
            field=models.DateTimeField(blank=True, null=True),
        ),
        SoltarIndiceDeCampo(
            model_name='cliente',
            name='indicado_por',
            field=models.ForeignKey(blank=True, db_column='cliente_indicador_id', db_comment='Quem indicou este cliente (cashback de indicacao em movimento_carteira).', db_index=False, help_text='Cliente que indicou este (programa fidelidade)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='indicacoes', to='aranha_estetica.cliente'),
        ),
        SoltarIndiceDeCampo(
            model_name='comprapacote',
            name='cliente',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='pacotes_comprados', to='aranha_estetica.cliente'),
        ),
        SoltarIndiceDeCampo(
            model_name='comprapacote',
            name='pacote',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.RESTRICT, to='aranha_estetica.pacote'),
        ),
        SoltarIndiceDeCampo(
            model_name='consumosessao',
            name='compra_pacote',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, related_name='sessoes_realizadas', to='aranha_estetica.comprapacote'),
        ),
        SoltarIndiceDeCampo(
            model_name='formularioanamnese',
            name='procedimento',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, to='aranha_estetica.procedimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='formularioanamnese',
            name='tipo',
            field=models.CharField(choices=[('ANAMNESE', 'Anamnese pre-atendimento'), ('PESQUISA', 'Pesquisa pos-atendimento'), ('PRONTUARIO', 'Questionario do prontuario (equipe)')], db_default='ANAMNESE', default='ANAMNESE', max_length=12),
        ),
        SoltarIndiceDeCampo(
            model_name='habilitacao',
            name='procedimento',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, to='aranha_estetica.procedimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='habilitacao',
            name='profissional',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, to='aranha_estetica.profissional'),
        ),
        SoltarIndiceDeCampo(
            model_name='itempacote',
            name='pacote',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, related_name='itens', to='aranha_estetica.pacote'),
        ),
        SoltarIndiceDeCampo(
            model_name='itempacote',
            name='procedimento',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, to='aranha_estetica.procedimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='listaespera',
            name='cliente',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, to='aranha_estetica.cliente'),
        ),
        SoltarIndiceDeCampo(
            model_name='listaespera',
            name='procedimento',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, to='aranha_estetica.procedimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='listaespera',
            name='profissional_desejado',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to='aranha_estetica.profissional'),
        ),
        SoltarIndiceDeCampo(
            model_name='logauditoria',
            name='usuario',
            field=models.ForeignKey(blank=True, db_column='usuario_autor_id', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
        ),
        SoltarIndiceDeCampo(
            model_name='movimentocarteira',
            name='atendimento',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, to='aranha_estetica.atendimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='movimentocarteira',
            name='carteira',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='movimentos', to='aranha_estetica.carteira'),
        ),
        SoltarIndiceDeCampo(
            model_name='movimentocarteira',
            name='usuario',
            field=models.ForeignKey(blank=True, db_column='usuario_autor_id', db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL),
        ),
        SoltarIndiceDeCampo(
            model_name='movimentocomissao',
            name='atendimento',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='comissoes', to='aranha_estetica.atendimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='movimentocomissao',
            name='profissional',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='comissoes', to='aranha_estetica.profissional'),
        ),
        SoltarIndiceDeCampo(
            model_name='movimentocomissao',
            name='regra',
            field=models.ForeignKey(blank=True, db_column='regra_comissao_id', db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, to='aranha_estetica.regracomissao'),
        ),
        SoltarIndiceDeCampo(
            model_name='notificacao',
            name='atendimento',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, to='aranha_estetica.atendimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='preco',
            name='procedimento',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, to='aranha_estetica.procedimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='preco',
            name='profissional',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, to='aranha_estetica.profissional'),
        ),
        SoltarIndiceDeCampo(
            model_name='procedimento',
            name='modalidade',
            field=models.CharField(choices=[('PRESENCIAL', 'Presencial'), ('ONLINE', 'Online'), ('HIBRIDO', 'Híbrido (presencial + online)')], db_default='PRESENCIAL', default='PRESENCIAL', max_length=12),
        ),
        SoltarIndiceDeCampo(
            model_name='promocao',
            name='procedimento',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, to='aranha_estetica.procedimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='prontuarioversao',
            name='autor',
            field=models.ForeignKey(blank=True, db_column='usuario_autor_id', db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
        ),
        SoltarIndiceDeCampo(
            model_name='prontuarioversao',
            name='prontuario',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='versoes', to='aranha_estetica.prontuario'),
        ),
        SoltarIndiceDeCampo(
            model_name='regracomissao',
            name='procedimento',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='regras_comissao', to='aranha_estetica.procedimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='regracomissao',
            name='profissional',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='regras_comissao', to='aranha_estetica.profissional'),
        ),
        SoltarIndiceDeCampo(
            model_name='respostaanamnese',
            name='atendimento',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, to='aranha_estetica.atendimento'),
        ),
        SoltarIndiceDeCampo(
            model_name='respostaanamnese',
            name='cliente',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, to='aranha_estetica.cliente'),
        ),
        SoltarIndiceDeCampo(
            model_name='respostaanamnese',
            name='formulario',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.RESTRICT, to='aranha_estetica.formularioanamnese'),
        ),
        SoltarIndiceDeCampo(
            model_name='versaotermo',
            name='procedimento',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, to='aranha_estetica.procedimento'),
        ),
        # ── 5. indices redundantes (§2.11) ──
        migrations.RemoveIndex(
            model_name='aceitetermo',
            name='idx_aceite_cli_data',
        ),
        migrations.RemoveIndex(
            model_name='assinaturapush',
            name='idx_pushsub_user_ativo',
        ),
        migrations.RemoveIndex(
            model_name='atendimento',
            name='idx_atendimento_cli_status',
        ),
        migrations.RemoveIndex(
            model_name='atendimento',
            name='idx_atendimento_status',
        ),
        migrations.RemoveIndex(
            model_name='cliente',
            name='idx_cliente_email_upper',
        ),
        migrations.RemoveIndex(
            model_name='cliente',
            name='idx_cliente_telefone',
        ),
        migrations.RemoveIndex(
            model_name='codigootp',
            name='idx_otp_email_data',
        ),
        migrations.RemoveIndex(
            model_name='codigootp',
            name='idx_otp_prop_data',
        ),
        migrations.RemoveIndex(
            model_name='formularioanamnese',
            name='idx_anamnese_ativo_escopo',
        ),
        migrations.RemoveIndex(
            model_name='formularioanamnese',
            name='idx_anamnese_ativo_tipo',
        ),
        migrations.RemoveIndex(
            model_name='listaespera',
            name='idx_espera_proc_data_notif',
        ),
        migrations.RemoveIndex(
            model_name='logauditoria',
            name='idx_auditoria_tabela',
        ),
        migrations.RemoveIndex(
            model_name='logauditoria',
            name='idx_auditoria_user_data',
        ),
        migrations.RemoveIndex(
            model_name='movimentocarteira',
            name='idx_movcred_origem_tipo',
        ),
        migrations.RemoveIndex(
            model_name='notificacao',
            name='idx_notif_nps_lookup',
        ),
        migrations.RemoveIndex(
            model_name='notificacao',
            name='idx_notificacao_tipo_status',
        ),
        migrations.RemoveIndex(
            model_name='preco',
            name='idx_preco_lookup',
        ),
        migrations.RemoveIndex(
            model_name='procedimento',
            name='idx_procedimento_ativo',
        ),
        migrations.RemoveIndex(
            model_name='procedimento',
            name='idx_procedimento_categoria',
        ),
        migrations.RemoveIndex(
            model_name='profissional',
            name='idx_profissional_ativo',
        ),
        migrations.RemoveIndex(
            model_name='promocao',
            name='idx_promocao_vigente',
        ),
        migrations.RemoveIndex(
            model_name='regracomissao',
            name='idx_regra_comissao_lookup',
        ),
        migrations.RemoveIndex(
            model_name='respostaanamnese',
            name='idx_anamnese_resp_data',
        ),
        # ── 5. RECRIA: UNIQUE parciais de cliente e teto de tentativas do OTP ──
        migrations.RemoveConstraint(
            model_name='cliente',
            name='uniq_cliente_email_ativo',
        ),
        migrations.RemoveConstraint(
            model_name='cliente',
            name='uniq_cliente_telefone_ativo',
        ),
        migrations.RemoveConstraint(
            model_name='cliente',
            name='uniq_cliente_cpf_ativo',
        ),
        migrations.RemoveConstraint(
            model_name='codigootp',
            name='chk_otp_tentativas_teto',
        ),
        migrations.AddConstraint(
            model_name='cliente',
            constraint=models.UniqueConstraint(condition=models.Q(('deletado_em__isnull', True)), fields=('telefone',), name='uq_cliente_telefone_ativo'),
        ),
        migrations.AddConstraint(
            model_name='cliente',
            constraint=models.UniqueConstraint(condition=models.Q(('deletado_em__isnull', True)), fields=('cpf',), name='uq_cliente_cpf_ativo'),
        ),
        migrations.AddConstraint(
            model_name='cliente',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Upper('email'), condition=models.Q(('deletado_em__isnull', True)), name='uq_cliente_email_ativo'),
        ),
        migrations.AddConstraint(
            model_name='codigootp',
            constraint=models.CheckConstraint(condition=models.Q(('tentativas__gte', 0), ('tentativas__lte', 10)), name='ck_codigo_otp_qtd_tentativas_0_10'),
        ),
        # ── 6. indices novos (§2.11) ──
        migrations.AddIndex(
            model_name='aceitetermo',
            index=aranha_estetica.db.indices.Indice(fields=['versao_termo'], name='ix_aceite_termo_versao_termo'),
        ),
        migrations.AddIndex(
            model_name='aceitetermo',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('atendimento__isnull', False)), fields=['atendimento'], name='ix_aceite_termo_atendimento_parcial'),
        ),
        migrations.AddIndex(
            model_name='assinaturapush',
            index=aranha_estetica.db.indices.Indice(fields=['user'], name='ix_assinatura_push_usuario'),
        ),
        migrations.AddIndex(
            model_name='atendimento',
            index=aranha_estetica.db.indices.Indice(fields=['cliente', 'data_hora_inicio'], name='ix_atendimento_cliente_data_hora_inicio'),
        ),
        migrations.AddIndex(
            model_name='atendimento',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('status__in', ['PENDENTE', 'AGENDADO', 'CONFIRMADO'])), fields=['data_hora_inicio'], name='ix_atendimento_data_hora_inicio_ativo_parcial'),
        ),
        migrations.AddIndex(
            model_name='atendimento',
            index=aranha_estetica.db.indices.Indice(fields=['procedimento'], name='ix_atendimento_procedimento'),
        ),
        migrations.AddIndex(
            model_name='atendimento',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('promocao__isnull', False)), fields=['promocao'], name='ix_atendimento_promocao_parcial'),
        ),
        migrations.AddIndex(
            model_name='atendimento',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('reagendado_de__isnull', False)), fields=['reagendado_de'], name='ix_atendimento_atendimento_reagendado_parcial'),
        ),
        migrations.AddIndex(
            model_name='atendimento',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('atendimento_origem__isnull', False)), fields=['atendimento_origem'], name='ix_atendimento_atendimento_origem_parcial'),
        ),
        migrations.AddIndex(
            model_name='avaliacaonps',
            index=aranha_estetica.db.indices.Indice(fields=['criado_em'], name='ix_avaliacao_nps_criado_em'),
        ),
        migrations.AddIndex(
            model_name='avaliacaonps',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('alerta_enviado', False), ('nota__lte', 6)), fields=['criado_em'], name='ix_avaliacao_nps_alerta_pendente_parcial'),
        ),
        migrations.AddIndex(
            model_name='avaliacaonps',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('aprovado_publicacao', True), ('autoriza_publicacao', True), ('nota__gte', 9)), fields=['-criado_em'], name='ix_avaliacao_nps_depoimento_parcial'),
        ),
        migrations.AddIndex(
            model_name='cliente',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('indicado_por__isnull', False)), fields=['indicado_por'], name='ix_cliente_cliente_indicador_parcial'),
        ),
        migrations.AddIndex(
            model_name='cliente',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('deletado_em__isnull', False)), fields=['deletado_em'], name='ix_cliente_deletado_em_parcial'),
        ),
        migrations.AddIndex(
            model_name='codigootp',
            index=aranha_estetica.db.indices.Indice(fields=['email', 'proposito', '-criado_em'], name='ix_codigo_otp_identificador_proposito_criado_em'),
        ),
        migrations.AddIndex(
            model_name='comprapacote',
            index=aranha_estetica.db.indices.Indice(fields=['pacote'], name='ix_compra_pacote_pacote'),
        ),
        migrations.AddIndex(
            model_name='comprapacote',
            index=aranha_estetica.db.indices.Indice(fields=['criado_em'], name='ix_compra_pacote_criado_em'),
        ),
        migrations.AddIndex(
            model_name='comprapacote',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('status', 'ATIVO')), fields=['data_expiracao'], name='ix_compra_pacote_data_expiracao_ativo_parcial'),
        ),
        migrations.AddIndex(
            model_name='formularioanamnese',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('procedimento__isnull', False)), fields=['procedimento'], name='ix_formulario_procedimento_parcial'),
        ),
        migrations.AddIndex(
            model_name='habilitacao',
            index=aranha_estetica.db.indices.Indice(fields=['procedimento'], name='ix_habilitacao_procedimento'),
        ),
        migrations.AddIndex(
            model_name='itempacote',
            index=aranha_estetica.db.indices.Indice(fields=['procedimento'], name='ix_pacote_item_procedimento'),
        ),
        migrations.AddIndex(
            model_name='listaespera',
            index=aranha_estetica.db.indices.Indice(fields=['procedimento', 'data_desejada'], name='ix_lista_espera_procedimento_data_desejada'),
        ),
        migrations.AddIndex(
            model_name='listaespera',
            index=aranha_estetica.db.indices.Indice(fields=['data_desejada'], name='ix_lista_espera_data_desejada'),
        ),
        migrations.AddIndex(
            model_name='listaespera',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('profissional_desejado__isnull', False)), fields=['profissional_desejado'], name='ix_lista_espera_profissional_desejado_parcial'),
        ),
        migrations.AddIndex(
            model_name='logauditoria',
            index=aranha_estetica.db.indices.Indice(fields=['tabela', 'registro_id'], name='ix_log_auditoria_tabela_registro'),
        ),
        migrations.AddIndex(
            model_name='movimentocarteira',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('atendimento__isnull', False)), fields=['atendimento'], name='ix_movimento_carteira_atendimento_parcial'),
        ),
        migrations.AddIndex(
            model_name='movimentocomissao',
            index=aranha_estetica.db.indices.Indice(fields=['atendimento'], name='ix_movimento_comissao_atendimento'),
        ),
        migrations.AddIndex(
            model_name='movimentocomissao',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('regra__isnull', False)), fields=['regra'], name='ix_movimento_comissao_regra_comissao_parcial'),
        ),
        migrations.AddIndex(
            model_name='preco',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('profissional__isnull', False)), fields=['profissional'], name='ix_preco_profissional_parcial'),
        ),
        migrations.AddIndex(
            model_name='promocao',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('procedimento__isnull', False)), fields=['procedimento'], name='ix_promocao_procedimento_parcial'),
        ),
        migrations.AddIndex(
            model_name='regracomissao',
            index=aranha_estetica.db.indices.Indice(fields=['profissional', 'procedimento'], name='ix_regra_comissao_profissional_procedimento'),
        ),
        migrations.AddIndex(
            model_name='regracomissao',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('procedimento__isnull', False)), fields=['procedimento'], name='ix_regra_comissao_procedimento_parcial'),
        ),
        migrations.AddIndex(
            model_name='respostaanamnese',
            index=aranha_estetica.db.indices.Indice(fields=['formulario'], name='ix_resposta_formulario_formulario'),
        ),
        migrations.AddIndex(
            model_name='versaotermo',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('procedimento__isnull', False)), fields=['procedimento'], name='ix_versao_termo_procedimento_parcial'),
        ),
        # ── 7. CHECKs novos (portaveis) ──
        migrations.AddConstraint(
            model_name='atendimento',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('valor_cobrado__isnull', True), ('valor_cobrado__gte', 0), _connector='OR'), models.Q(('valor_original__isnull', True), ('valor_original__gte', 0), _connector='OR')), name='ck_atendimento_valores_nao_negativos'),
        ),
        migrations.AddConstraint(
            model_name='avaliacaonps',
            constraint=models.CheckConstraint(condition=models.Q(('aprovado_publicacao', False), ('autoriza_publicacao', True), _connector='OR'), name='ck_avaliacao_nps_aprovacao_exige_autorizacao'),
        ),
        migrations.AddConstraint(
            model_name='cliente',
            constraint=models.CheckConstraint(condition=models.Q(('faltas_consecutivas__gte', 0)), name='ck_cliente_qtd_faltas_nao_negativa'),
        ),
        migrations.AddConstraint(
            model_name='cliente',
            constraint=models.CheckConstraint(condition=models.Q(('email__isnull', True), models.Q(('email', ''), _negated=True), _connector='OR'), name='ck_cliente_email_nao_vazio'),
        ),
        migrations.AddConstraint(
            model_name='codigootp',
            constraint=models.CheckConstraint(condition=models.Q(('canal__in', ['EMAIL', 'SMS'])), name='ck_codigo_otp_canal'),
        ),
        migrations.AddConstraint(
            model_name='codigootp',
            constraint=models.CheckConstraint(condition=models.Q(('proposito__in', ['AGENDAMENTO', 'LOGIN_CLIENTE', 'DSAR'])), name='ck_codigo_otp_proposito'),
        ),
        migrations.AddConstraint(
            model_name='listaespera',
            constraint=models.CheckConstraint(condition=models.Q(('email_contato__isnull', True), models.Q(('email_contato', ''), _negated=True), _connector='OR'), name='ck_lista_espera_email_contato_nao_vazio'),
        ),
        migrations.AddConstraint(
            model_name='movimentocarteira',
            constraint=models.CheckConstraint(condition=models.Q(('tipo__in', ['CREDITO', 'DEBITO'])), name='ck_movimento_carteira_tipo'),
        ),
        migrations.AddConstraint(
            model_name='movimentocarteira',
            constraint=models.CheckConstraint(condition=models.Q(('origem__in', ['VALE_PRESENTE', 'CANCELAMENTO', 'AJUSTE_MANUAL', 'USO_AGENDAMENTO', 'CASHBACK_INDICACAO', 'CASHBACK_ESTORNO', 'OUTRO'])), name='ck_movimento_carteira_origem'),
        ),
        migrations.AddConstraint(
            model_name='movimentocarteira',
            constraint=models.CheckConstraint(condition=models.Q(('valor__gt', 0)), name='ck_movimento_carteira_valor_positivo'),
        ),
        migrations.AddConstraint(
            model_name='movimentocarteira',
            constraint=models.CheckConstraint(condition=models.Q(('saldo_resultante__gte', 0)), name='ck_movimento_carteira_valor_saldo_resultante_nao_negativo'),
        ),
        migrations.AddConstraint(
            model_name='notificacao',
            constraint=models.CheckConstraint(condition=models.Q(('tipo__in', ['LEMBRETE', 'LEMBRETE_2H', 'CONFIRMACAO', 'CANCELAMENTO', 'NPS', 'PESQUISA', 'APROVACAO', 'TERMO'])), name='ck_notificacao_tipo'),
        ),
        migrations.AddConstraint(
            model_name='notificacao',
            constraint=models.CheckConstraint(condition=models.Q(('canal__in', ['WHATSAPP', 'SMS', 'EMAIL'])), name='ck_notificacao_canal'),
        ),
        migrations.AddConstraint(
            model_name='notificacao',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['PENDENTE', 'ENVIADO', 'FALHOU'])), name='ck_notificacao_status'),
        ),
        migrations.AddConstraint(
            model_name='notificacao',
            constraint=models.CheckConstraint(condition=models.Q(('resposta__isnull', True), ('resposta__in', ['CONFIRMOU', 'CANCELOU']), _connector='OR'), name='ck_notificacao_resposta'),
        ),
        migrations.AddConstraint(
            model_name='procedimento',
            constraint=models.CheckConstraint(condition=models.Q(('retorno_minimo_dias__gte', 0)), name='ck_procedimento_retorno_minimo_dias_nao_negativo'),
        ),
        migrations.AddConstraint(
            model_name='procedimento',
            constraint=models.CheckConstraint(condition=models.Q(('retorno_maximo_dias__gte', 0)), name='ck_procedimento_retorno_maximo_dias_nao_negativo'),
        ),
        migrations.AddConstraint(
            model_name='procedimento',
            constraint=models.CheckConstraint(condition=models.Q(('duracao_retorno_minutos__gte', 0)), name='ck_procedimento_duracao_retorno_nao_negativa'),
        ),
        migrations.AddConstraint(
            model_name='regracomissao',
            constraint=models.CheckConstraint(condition=models.Q(('valor__isnull', True), ('valor__gte', 0), _connector='OR'), name='ck_regra_comissao_valor_fixo_nao_negativo'),
        ),
        # ── 8. PG: CHECK regex/jsonb, DROP do GIN sem uso ──
        migrations.RunPython(aplicar_checks_pg, remover_checks_pg),
        # ── 9. PG: PK/FK (ON DELETE real)/UNIQUE de OneToOne/sequencias/_like em todas as tabelas ──
        PadronizarNomes(),
        # ── 10. COMMENT de tabela e de coluna (§2) ──
        migrations.RunPython(migrations.RunPython.noop, restaurar_comentarios_0038),
        # ── 10. COMMENT ON TABLE ──
        migrations.AlterModelTableComment(
            name='aceitetermo',
            table_comment='Prova do aceite (LGPD art. 8): IP, user-agent e SHA-256 do texto lido. Imutavel (tg_aceite_termo_imutavel); retencao 20 anos.',
        ),
        migrations.AlterModelTableComment(
            name='anotacaosessao',
            table_comment='Nota clinica de um atendimento, append-only (correcao = nova anotacao; tg_anotacao_sessao_imutavel).',
        ),
        migrations.AlterModelTableComment(
            name='assinaturapush',
            table_comment='Inscricao Web Push (VAPID) de um usuario da equipe.',
        ),
        migrations.AlterModelTableComment(
            name='atendimento',
            table_comment='Agendamento/sessao. FSM (models.Atendimento.TRANSICOES): PENDENTE->AGENDADO->CONFIRMADO->REALIZADO|CANCELADO|FALTOU|REAGENDADO. ex_atendimento_sobreposicao impede 2 ativos sobrepostos do mesmo profissional.',
        ),
        migrations.AlterModelTableComment(
            name='avaliacaonps',
            table_comment='Nota 0-10 do atendimento. Depoimento publico exige opt-in do cliente (eh_publicacao_autorizada) E moderacao (eh_publicacao_aprovada).',
        ),
        migrations.AlterModelTableComment(
            name='carteira',
            table_comment='Saldo de credito do cliente (cashback, vale). Toda mutacao: select_for_update + movimento_carteira na mesma transacao.',
        ),
        migrations.AlterModelTableComment(
            name='cliente',
            table_comment='Cliente da clinica. Telefone (so digitos, 10-11) e a chave natural do agendamento publico (uq_cliente_telefone_ativo). Soft delete em deletado_em; anonimizacao LGPD troca nome por [ANONIMIZADO-<id>] (sp_anonimizar_cliente). Consentimentos em consentimento.',
        ),
        migrations.AlterModelTableComment(
            name='codigootp',
            table_comment='Desafio OTP (HMAC-SHA256 com a SECRET_KEY), TTL e lockout. Efemero: apagado apos 24h (sp_purgar_dados_expirados).',
        ),
        migrations.AlterModelTableComment(
            name='comprapacote',
            table_comment='Venda de pacote (registro fiscal, PROTECT). Receita = valor_pago - valor_reembolsado na data da venda (D20).',
        ),
        migrations.AlterModelTableComment(
            name='configuracao',
            table_comment='Chave-valor editavel no painel: Branding, email_admin, MAX_FALTAS_BLOQUEIO. (prontuario_perguntas saiu p/ formulario tipo PRONTUARIO.)',
        ),
        migrations.AlterModelTableComment(
            name='consumosessao',
            table_comment='1 sessao de pacote consumida por atendimento REALIZADO (nunca retorno). tg_consumo_sessao_validar confere item da compra, procedimento, retorno e saldo.',
        ),
        migrations.AlterModelTableComment(
            name='formularioanamnese',
            table_comment='Questionario unico: ANAMNESE (pre-atendimento, respondida pela cliente), PESQUISA (pos-atendimento) e PRONTUARIO (perguntas extras da ficha, preenchidas pela equipe; no maximo 1 ativo).',
        ),
        migrations.AlterModelTableComment(
            name='habilitacao',
            table_comment='Profissional x procedimento que ele realiza. Sem nenhuma linha p/ um procedimento, qualquer profissional ativo atende (services/disponibilidade.profissionais_para).',
        ),
        migrations.AlterModelTableComment(
            name='itempacote',
            table_comment='Itens do pacote no catalogo (procedimento x sessoes).',
        ),
        migrations.AlterModelTableComment(
            name='listaespera',
            table_comment='Pedido de aviso de vaga. Apagado quando data_desejada passa (sp_aplicar_retencao_lgpd).',
        ),
        migrations.AlterModelTableComment(
            name='logauditoria',
            table_comment='Trilha de ACOES da aplicacao (LGPD art. 37): autor + snapshot "Nome <email>", acao, alvo, IP. Retencao 5 anos. A trilha de DADOS (antes/depois) fica em historico_alteracao.',
        ),
        migrations.AlterModelTableComment(
            name='movimentocarteira',
            table_comment='Ledger append-only da carteira (tg_movimento_carteira_imutavel). Estorno = novo movimento.',
        ),
        migrations.AlterModelTableComment(
            name='movimentocomissao',
            table_comment='Ledger append-only de comissao: CREDITO (atendimento realizado), ESTORNO (cancelamento) e PAGAMENTO (baixa), estes dois referenciando o CREDITO. Status e saldo sao derivados (vw_comissao, vw_comissao_saldo). Imutavel (tg_movimento_comissao_imutavel).',
        ),
        migrations.AlterModelTableComment(
            name='notificacao',
            table_comment='Mensagem ligada a um atendimento (lembrete, NPS, termo...). O link publico da mensagem vive em link_acesso (so o hash). mensagem e limpa apos 12 meses.',
        ),
        migrations.AlterModelTableComment(
            name='pacote',
            table_comment='Catalogo de pacotes, editavel a qualquer momento: a venda congela nome/itens em compra_pacote/compra_pacote_item.',
        ),
        migrations.AlterModelTableComment(
            name='preco',
            table_comment='Preco versionado por data_inicio_vigencia: base (profissional NULL) ou por profissional. Vale a vigencia mais recente <= data do atendimento (utils/precos).',
        ),
        migrations.AlterModelTableComment(
            name='procedimento',
            table_comment='Catalogo de servicos. tem_retorno_obrigatorio dispara o retorno gratuito (RetornoService).',
        ),
        migrations.AlterModelTableComment(
            name='profissional',
            table_comment='Quem atende. Grade semanal em agenda_horario, folgas/bloqueios em agenda_excecao. Feed ICS por link_acesso (finalidade FEED_ICS).',
        ),
        migrations.AlterModelTableComment(
            name='promocao',
            table_comment='Desconto percentual XOR preco fixo num periodo. procedimento NULL = promocao geral (so percentual).',
        ),
        migrations.AlterModelTableComment(
            name='prontuario',
            table_comment='Ficha clinica base 1:1 com o cliente (retencao 20 anos). Cada edicao grava antes a foto anterior em prontuario_versao; historico_alteracao registra qualquer escrita (inclusive fora da app).',
        ),
        migrations.AlterModelTableComment(
            name='prontuarioversao',
            table_comment='Historico append-only do prontuario (CFM 1.638/2002): foto do estado ANTERIOR a cada edicao. Imutavel (tg_prontuario_versao_imutavel; so anular usuario_autor_id e aceito).',
        ),
        migrations.AlterModelTableComment(
            name='regracomissao',
            table_comment='Regra percentual XOR valor fixo por (profissional, procedimento); NULL = qualquer. Mais especifica vence; empate = atualizada por ultimo. Regra ja usada nao se exclui (FK de movimento_comissao): desative.',
        ),
        migrations.AlterModelTableComment(
            name='respostaanamnese',
            table_comment='Ficha ANAMNESE/PESQUISA respondida pela cliente (dado de saude quando ANAMNESE). respondido_em NULL = convite nao respondido. O link publico vive em link_acesso (FORMULARIO).',
        ),
        migrations.AlterModelTableComment(
            name='usuario',
            table_comment='Equipe (login por e-mail). papel ADMIN/PROFISSIONAL/RECEPCAO; is_staff = ADMIN. Nunca excluir: desativar (eh_ativo=false).',
        ),
        migrations.AlterModelTableComment(
            name='versaotermo',
            table_comment='Texto versionado de termo (LGPD, SAUDE art. 11, PROCEDIMENTO). Imutavel depois do 1o aceite (tg_versao_termo_imutavel); 1 ativo por escopo.',
        ),
        # ── 10. COMMENT ON COLUMN ──
        ComentarColuna(
            model_name='aceitetermo',
            name='conteudo_sha256',
            field=aranha_estetica.db.campos.HashSha256Field(blank=True, db_column='conteudo_hash', db_comment='SHA-256 do versao_termo.conteudo aceito; NULL = aceite anterior a 0044 (sem prova do texto).', null=True),
        ),
        ComentarColuna(
            model_name='assinaturapush',
            name='auth',
            field=models.CharField(db_column='chave_auth', db_comment='Segredo de autenticacao do navegador (PushManager.subscribe).', max_length=80),
        ),
        ComentarColuna(
            model_name='assinaturapush',
            name='p256dh',
            field=models.CharField(db_column='chave_p256dh', db_comment='Chave publica do navegador (PushManager.subscribe).', max_length=140),
        ),
        ComentarColuna(
            model_name='atendimento',
            name='valor_cobrado',
            field=models.DecimalField(blank=True, db_comment='Preco gravado no agendamento (promocao da data); sessao de pacote leva o preco cheio e nao soma no faturamento (D20).', decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        ComentarColuna(
            model_name='atendimento',
            name='valor_original',
            field=models.DecimalField(blank=True, db_comment='Preco cheio quando houve promocao.', decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        ComentarColuna(
            model_name='avaliacaonps',
            name='alerta_enviado',
            field=models.BooleanField(db_column='eh_alerta_enviado', db_comment='Detrator (nota <= 6) ja avisado por e-mail a clinica.', db_default=False, default=False),
        ),
        ComentarColuna(
            model_name='cliente',
            name='faltas_consecutivas',
            field=models.SmallIntegerField(db_column='qtd_faltas_consecutivas', db_comment='Zera ao REALIZADO; ao atingir MAX_FALTAS_BLOQUEIO liga eh_bloqueado_online.', db_default=0, default=0, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        ComentarColuna(
            model_name='cliente',
            name='nome',
            field=models.CharField(db_collation='pt_br', db_comment='COLLATE pt_br (ICU) - ordenacao correta de acentos.', max_length=150),
        ),
        ComentarColuna(
            model_name='codigootp',
            name='email',
            field=models.EmailField(db_column='identificador', db_comment='Chave do desafio: e-mail, ou "sms+<digitos>@shivazen.local" nos fluxos so-telefone.', max_length=254),
        ),
        ComentarColuna(
            model_name='comprapacote',
            name='data_expiracao',
            field=models.DateField(blank=True, db_comment='Ultimo dia valido (data local); sessao feita ate esse dia debita mesmo com o job ja tendo marcado EXPIRADO.', null=True),
        ),
        ComentarColuna(
            model_name='formularioanamnese',
            name='schema_json',
            field=models.JSONField(db_column='perguntas', db_comment='Lista [{key, tipo (bool|text|longtext|select|scale|checkboxes|number|date|email), label, obrigatorio, opcoes?, alerta_saude?}] - validada por services/anamnese.validar_respostas.', db_default=[], default=list),
        ),
        ComentarColuna(
            model_name='listaespera',
            name='email_contato',
            field=models.EmailField(blank=True, db_comment='E-mail digitado no formulario publico quando o cliente ja existe (nao reescreve o cadastro).', max_length=254, null=True),
        ),
        ComentarColuna(
            model_name='listaespera',
            name='turno_desejado',
            field=models.CharField(blank=True, choices=[('MANHA', 'Manhã'), ('TARDE', 'Tarde'), ('NOITE', 'Noite')], db_comment='NULL = qualquer turno.', max_length=20, null=True),
        ),
        ComentarColuna(
            model_name='logauditoria',
            name='registro_id',
            field=models.BigIntegerField(blank=True, db_column='registro_pk', db_comment='PK do registro afetado na tabela indicada em "tabela" (referencia polimorfica, sem FK).', null=True),
        ),
        ComentarColuna(
            model_name='logauditoria',
            name='usuario_nome',
            field=models.CharField(blank=True, db_column='usuario_autor_nome', db_comment='Snapshot "Nome <email>" do autor no momento da acao.', db_default='', default='', max_length=255),
        ),
        ComentarColuna(
            model_name='procedimento',
            name='buffer_minutos',
            field=models.SmallIntegerField(db_column='intervalo_minutos', db_comment='Folga apos o procedimento (limpeza/preparo) que tambem ocupa a agenda (0-120).', db_default=0, default=0),
        ),
        ComentarColuna(
            model_name='procedimento',
            name='duracao_retorno_minutos',
            field=models.SmallIntegerField(db_comment='0 = usa a duracao padrao do retorno (30 min).', db_default=30, default=30, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        ComentarColuna(
            model_name='profissional',
            name='gcal_calendar_id',
            field=models.CharField(blank=True, db_column='gcal_calendario_ref', db_comment='Id do calendario no Google (externo).', db_default='primary', default='primary', max_length=200),
        ),
        ComentarColuna(
            model_name='profissional',
            name='gcal_refresh_token',
            field=models.TextField(blank=True, db_column='gcal_token_renovacao', db_comment='Refresh token OAuth do Google Calendar - segredo reversivel (nao da p/ hashear); ver padrao-sql secao 7.3 (cifrar).', db_default='', default=''),
        ),
        ComentarColuna(
            model_name='profissional',
            name='max_advance_dias',
            field=models.SmallIntegerField(db_column='antecedencia_maxima_dias', db_comment='Dias maximos a frente oferecidos online.', db_default=60, default=60),
        ),
        ComentarColuna(
            model_name='profissional',
            name='min_notice_horas',
            field=models.SmallIntegerField(db_column='antecedencia_minima_horas', db_comment='Horas minimas entre agora e o inicio de um horario oferecido online.', db_default=2, default=2),
        ),
        ComentarColuna(
            model_name='promocao',
            name='imagem_url',
            field=models.TextField(blank=True, db_column='url_imagem', db_comment='text (legado sem limite de tamanho): excecao ao varchar das url_*.', db_default='', default=''),
        ),
        ComentarColuna(
            model_name='prontuario',
            name='respostas_extras',
            field=models.JSONField(blank=True, db_column='respostas', db_comment='Respostas do questionario do prontuario {key: valor}; keys = formulario.perguntas do formulario tipo PRONTUARIO ativo.', db_default={}, default=dict),
        ),
        ComentarColuna(
            model_name='prontuarioversao',
            name='dados',
            field=models.JSONField(db_comment='Chaves = atributos Python (CAMPOS_CLINICOS + respostas_extras). E registro historico: nunca renomear as chaves.', db_default={}, default=dict),
        ),
        ComentarColuna(
            model_name='usuario',
            name='password',
            field=models.CharField(db_column='senha_hash', db_comment='Hash do Django (pbkdf2_sha256$...). Excecao a regra char(64) de *_hash.', max_length=255),
        ),
        ComentarColuna(
            model_name='usuario',
            name='profissional',
            field=models.OneToOneField(blank=True, db_comment='Profissional que este login representa (1:1 opcional).', null=True, on_delete=django.db.models.deletion.SET_NULL, to='aranha_estetica.profissional'),
        ),
    ]
