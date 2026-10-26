# Padrao SQL — fase de nomes N1: tabelas e colunas (docs/specs/padrao-sql.md §1, §2, §3.1, §3.2, §3.4, §6)
#
# Nome so no banco: atributos e classes Python NAO mudam; muda db_table/db_column (estado) e o
# catalogo (banco). Ordem (§6, N1):
#   1. PG: derruba os triggers/funcoes de imutabilidade da 0038/0046 (corpo PL/pgSQL e TEXTO:
#      cita ip/conteudo_sha256/autor_id/autor_nome e quebraria no 1o UPDATE depois do rename; e o
#      ''->NULL do conteudo_hash bateria no trg_aceite_termo_imutavel)
#   2. AlterModelTable x3 (item_pacote -> pacote_item, formulario_anamnese -> formulario,
#      resposta_anamnese -> resposta_formulario) + PG: sequencias <tabela>_id_seq
#   3. 64 RenomearColuna (§3.2): ALTER TABLE ... RENAME COLUMN (so catalogo; FK nao e recriada)
#   4. tipos: log_auditoria.registro_pk integer -> bigint; codigo_otp.codigo_hash e
#      aceite_termo.conteudo_hash varchar(64) -> char(64) (HashSha256Field; conteudo_hash NULL)
#   5. dados (RunPython, portavel): ''->NULL (conteudo_hash, cliente.email/cpf/telefone,
#      lista_espera.email_contato), NULL->'' nos textos que viram NOT NULL, atualizado_em NULL ->
#      criado_em (atendimento, cliente, formulario), log_auditoria.tabela com o nome novo das 3
#      tabelas (referencia polimorfica tabela + registro_pk); LogAuditoria com as contagens (so se
#      mudou algo); SET CONSTRAINTS ALL IMMEDIATE
#   6. AlterField NOT NULL (+ DEFAULT '') e atualizado_em NOT NULL
#   7. AddField criado_em/atualizado_em onde faltam (DEFAULT now(): linha antiga = instante da
#      migration, dito no COMMENT da coluna)
#   8. db_default nas demais colunas (§1.2: todo default literal/now() tambem no banco)
#   9. Positive*Field -> SmallIntegerField (o Django solta o CHECK automatico <t>_<col>_check; o
#      ck_..._nao_negativo / ck_codigo_otp_qtd_tentativas_0_10 nomeado vem na 0055)
#  10. collation pt_br no estado (§3.6.3): garante a collation (ICU) e AlterField db_collation
#  11. PG: fn_bloquear_mutacao (CREATE OR REPLACE, = 0052), fn_aceite_termo_imutavel,
#      fn_versao_termo_imutavel, fn_prontuario_versao_imutavel + tg_* (corpos do §5.2: colunas
#      novas, SQLSTATE 23000 -> IntegrityError); tg_movimento_carteira_imutavel passa a usar a
#      fn_bloquear_mutacao generica.
#
# Nao aborta (nada a validar: CHECKs novos e VALIDATE ficam na 0055). Exige ICU no PG (a collation
# pt_br); sem ICU aborta com mensagem clara.
#
# Reverso (dev/testes; rollback de producao = restaurar o dump): cada passo tem o seu — triggers
# novos derrubados, colunas/tabelas/sequencias com o nome antigo, NOT NULL desfeito (o '' fica),
# triggers e funcoes da 0038/0046 recriados com os corpos originais.

from decimal import Decimal

import django.core.validators
import django.db.models.functions.datetime
from django.db import NotSupportedError, migrations, models, transaction
from django.db.models import F

import aranha_estetica.db.campos
from aranha_estetica.db.operacoes import RenomearColuna

APP = 'aranha_estetica'

# ─── 1. triggers legados (0038/0046) ────────────────────────────────────────
SQL_DERRUBAR_LEGADOS = [
    'DROP TRIGGER IF EXISTS trg_movimento_carteira_imutavel ON movimento_carteira',
    'DROP TRIGGER IF EXISTS trg_aceite_termo_imutavel ON aceite_termo',
    'DROP TRIGGER IF EXISTS trg_versao_termo_imutavel ON versao_termo',
    'DROP TRIGGER IF EXISTS trg_prontuario_versao_imutavel ON prontuario_versao',
    'DROP FUNCTION IF EXISTS bloquear_mutacao_ledger()',
    'DROP FUNCTION IF EXISTS aceite_termo_imutavel()',
    'DROP FUNCTION IF EXISTS versao_termo_imutavel()',
    'DROP FUNCTION IF EXISTS prontuario_versao_imutavel()',
]

# reverso: os corpos ORIGINAIS (0038 e 0046), com os nomes de coluna antigos
SQL_LEGADOS = [
    """
    CREATE OR REPLACE FUNCTION bloquear_mutacao_ledger() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'movimento de ledger e imutavel (%.id=%)', TG_TABLE_NAME, OLD.id;
    END $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_movimento_carteira_imutavel
    BEFORE UPDATE OR DELETE ON movimento_carteira
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacao_ledger()
    """,
    """
    CREATE OR REPLACE FUNCTION aceite_termo_imutavel() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'aceite_termo e prova legal (LGPD art. 8): exclusao bloqueada'
                USING DETAIL = 'aceite_termo.id=' || OLD.id;
        END IF;
        IF (NEW.id, NEW.cliente_id, NEW.versao_termo_id, NEW.ip, NEW.user_agent,
            NEW.conteudo_sha256, NEW.criado_em)
           IS DISTINCT FROM
           (OLD.id, OLD.cliente_id, OLD.versao_termo_id, OLD.ip, OLD.user_agent,
            OLD.conteudo_sha256, OLD.criado_em)
           OR (NEW.atendimento_id IS DISTINCT FROM OLD.atendimento_id
               AND NEW.atendimento_id IS NOT NULL) THEN
            RAISE EXCEPTION 'aceite_termo e prova legal (LGPD art. 8): registro imutavel'
                USING DETAIL = 'aceite_termo.id=' || OLD.id;
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_aceite_termo_imutavel
    BEFORE UPDATE OR DELETE ON aceite_termo
    FOR EACH ROW EXECUTE FUNCTION aceite_termo_imutavel()
    """,
    """
    CREATE OR REPLACE FUNCTION versao_termo_imutavel() RETURNS trigger AS $$
    BEGIN
        IF (NEW.id, NEW.tipo, NEW.procedimento_id, NEW.titulo, NEW.conteudo, NEW.versao)
           IS DISTINCT FROM
           (OLD.id, OLD.tipo, OLD.procedimento_id, OLD.titulo, OLD.conteudo, OLD.versao)
           AND EXISTS (SELECT 1 FROM aceite_termo WHERE versao_termo_id = OLD.id) THEN
            RAISE EXCEPTION 'versao_termo ja aceita e imutavel: publique uma nova versao'
                USING DETAIL = 'versao_termo.id=' || OLD.id;
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_versao_termo_imutavel
    BEFORE UPDATE ON versao_termo
    FOR EACH ROW EXECUTE FUNCTION versao_termo_imutavel()
    """,
    """
    CREATE OR REPLACE FUNCTION prontuario_versao_imutavel() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'prontuario_versao e historico clinico: exclusao bloqueada'
                USING DETAIL = 'prontuario_versao.id=' || OLD.id;
        END IF;
        IF (NEW.id, NEW.prontuario_id, NEW.autor_nome, NEW.criado_em, NEW.dados)
           IS DISTINCT FROM
           (OLD.id, OLD.prontuario_id, OLD.autor_nome, OLD.criado_em, OLD.dados)
           OR (NEW.autor_id IS DISTINCT FROM OLD.autor_id AND NEW.autor_id IS NOT NULL) THEN
            RAISE EXCEPTION 'prontuario_versao e historico clinico: registro imutavel'
                USING DETAIL = 'prontuario_versao.id=' || OLD.id;
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_prontuario_versao_imutavel
    BEFORE UPDATE OR DELETE ON prontuario_versao
    FOR EACH ROW EXECUTE FUNCTION prontuario_versao_imutavel()
    """,
]

# ─── 11. funcoes/triggers com o nome e as colunas novas (§3.4, §5.2) ──────────
SQL_FUNCOES = [
    # = 0052 (generica; CREATE OR REPLACE nao muda nada se ja existe)
    """
CREATE OR REPLACE FUNCTION fn_bloquear_mutacao() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% e append-only: % bloqueado', TG_TABLE_NAME, TG_OP
        USING DETAIL = TG_TABLE_NAME || '.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
END $$;
""",
    'CREATE TRIGGER tg_movimento_carteira_imutavel BEFORE UPDATE OR DELETE ON movimento_carteira '
    'FOR EACH ROW EXECUTE FUNCTION fn_bloquear_mutacao()',
    """
CREATE OR REPLACE FUNCTION fn_aceite_termo_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'aceite_termo e prova legal (LGPD art. 8): exclusao bloqueada'
            USING DETAIL = 'aceite_termo.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.id, NEW.cliente_id, NEW.versao_termo_id, NEW.ip_origem, NEW.user_agent, NEW.conteudo_hash, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.cliente_id, OLD.versao_termo_id, OLD.ip_origem, OLD.user_agent, OLD.conteudo_hash, OLD.criado_em)
       OR (NEW.atendimento_id IS DISTINCT FROM OLD.atendimento_id AND NEW.atendimento_id IS NOT NULL) THEN
        RAISE EXCEPTION 'aceite_termo e prova legal (LGPD art. 8): registro imutavel'
            USING DETAIL = 'aceite_termo.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;                                  -- so o SET NULL de atendimento_id passa
END $$;
""",
    'CREATE TRIGGER tg_aceite_termo_imutavel BEFORE UPDATE OR DELETE ON aceite_termo '
    'FOR EACH ROW EXECUTE FUNCTION fn_aceite_termo_imutavel()',
    """
CREATE OR REPLACE FUNCTION fn_versao_termo_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.id, NEW.tipo, NEW.procedimento_id, NEW.titulo, NEW.conteudo, NEW.versao)
       IS DISTINCT FROM (OLD.id, OLD.tipo, OLD.procedimento_id, OLD.titulo, OLD.conteudo, OLD.versao)
       AND EXISTS (SELECT 1 FROM aceite_termo WHERE versao_termo_id = OLD.id) THEN
        RAISE EXCEPTION 'versao_termo ja aceita e imutavel: publique uma nova versao'
            USING DETAIL = 'versao_termo.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;                                  -- eh_ativo / data_inicio_vigencia podem mudar
END $$;
""",
    'CREATE TRIGGER tg_versao_termo_imutavel BEFORE UPDATE ON versao_termo '
    'FOR EACH ROW EXECUTE FUNCTION fn_versao_termo_imutavel()',
    """
CREATE OR REPLACE FUNCTION fn_prontuario_versao_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'prontuario_versao e historico clinico: exclusao bloqueada'
            USING DETAIL = 'prontuario_versao.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.id, NEW.prontuario_id, NEW.usuario_autor_nome, NEW.criado_em, NEW.dados)
       IS DISTINCT FROM (OLD.id, OLD.prontuario_id, OLD.usuario_autor_nome, OLD.criado_em, OLD.dados)
       OR (NEW.usuario_autor_id IS DISTINCT FROM OLD.usuario_autor_id AND NEW.usuario_autor_id IS NOT NULL) THEN
        RAISE EXCEPTION 'prontuario_versao e historico clinico: registro imutavel'
            USING DETAIL = 'prontuario_versao.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
""",
    'CREATE TRIGGER tg_prontuario_versao_imutavel BEFORE UPDATE OR DELETE ON prontuario_versao '
    'FOR EACH ROW EXECUTE FUNCTION fn_prontuario_versao_imutavel()',
]
SQL_FUNCOES_REVERSO = [
    'DROP TRIGGER IF EXISTS tg_prontuario_versao_imutavel ON prontuario_versao',
    'DROP FUNCTION IF EXISTS fn_prontuario_versao_imutavel()',
    'DROP TRIGGER IF EXISTS tg_versao_termo_imutavel ON versao_termo',
    'DROP FUNCTION IF EXISTS fn_versao_termo_imutavel()',
    'DROP TRIGGER IF EXISTS tg_aceite_termo_imutavel ON aceite_termo',
    'DROP FUNCTION IF EXISTS fn_aceite_termo_imutavel()',
    # fn_bloquear_mutacao e da 0052 (compra_pacote_item/movimento_comissao a usam): fica
    'DROP TRIGGER IF EXISTS tg_movimento_carteira_imutavel ON movimento_carteira',
]

# ─── 2. sequencias das tabelas renomeadas (§3.1) ────────────────────────────
TABELAS_RENOMEADAS = (
    ('item_pacote', 'pacote_item'),
    ('formulario_anamnese', 'formulario'),
    ('resposta_anamnese', 'resposta_formulario'),
)

# ─── 5. dados: (model, atributo) ────────────────────────────────────────────
VAZIO_PARA_NULO = (
    ('Cliente', 'email'), ('Cliente', 'cpf'), ('Cliente', 'telefone'),
    ('ListaEspera', 'email_contato'), ('AceiteTermo', 'conteudo_sha256'),
)
NULO_PARA_VAZIO = (
    ('Profissional', 'especialidade'),
    ('Procedimento', 'descricao'), ('Procedimento', 'descricao_longa'), ('Procedimento', 'imagem_destaque'),
    ('Promocao', 'descricao'), ('Promocao', 'imagem_url'),
    ('Preco', 'descricao'),
    ('Atendimento', 'descricao_preco'),
    ('Notificacao', 'mensagem'),
    ('AvaliacaoNPS', 'comentario'),
    ('Prontuario', 'alergias'), ('Prontuario', 'contraindicacoes'), ('Prontuario', 'historico_saude'),
    ('Prontuario', 'medicamentos_uso'), ('Prontuario', 'observacoes_gerais'),
    ('Configuracao', 'valor'), ('Configuracao', 'descricao'),
    ('LogAuditoria', 'tabela'),
    ('Pacote', 'descricao'),
)
ATUALIZADO_EM_PELO_CRIADO_EM = ('Atendimento', 'Cliente', 'FormularioAnamnese')
# log_auditoria.tabela + registro_pk e referencia polimorfica: segue o rename da tabela (§3.1)
TABELA_NO_LOG = {velha: nova for velha, nova in TABELAS_RENOMEADAS}


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def _executar(schema_editor, comandos):
    for sql in comandos:
        schema_editor.execute(sql, None)  # params=None: o '%' do RAISE nao e placeholder


def derrubar_triggers_legados(apps, schema_editor):
    if _pg(schema_editor):
        _executar(schema_editor, SQL_DERRUBAR_LEGADOS)


def recriar_triggers_legados(apps, schema_editor):
    if _pg(schema_editor):
        _executar(schema_editor, SQL_LEGADOS)


def _renomear_sequencias(schema_editor, de_para):
    if not _pg(schema_editor):
        return
    for tabela, antiga, nova in de_para:
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_serial_sequence(%s, 'id')", [tabela])
            (seq,) = cursor.fetchone()
        if seq and seq.split('.')[-1].strip('"') == antiga:
            schema_editor.execute(f'ALTER SEQUENCE {seq} RENAME TO {schema_editor.quote_name(nova)}', None)


def renomear_sequencias(apps, schema_editor):
    # a tabela ja tem o nome novo (AlterModelTable acima)
    _renomear_sequencias(schema_editor, [(nova, f'{velha}_id_seq', f'{nova}_id_seq')
                                         for velha, nova in TABELAS_RENOMEADAS])


def desfazer_sequencias(apps, schema_editor):
    # reverso: roda antes do AlterModelTable reverso (a tabela ainda tem o nome novo)
    _renomear_sequencias(schema_editor, [(nova, f'{nova}_id_seq', f'{velha}_id_seq')
                                         for velha, nova in TABELAS_RENOMEADAS])


def normalizar_dados(apps, schema_editor):
    """''/NULL na forma do padrao (§1.2 Texto, §3.2 Nulidade) antes dos NOT NULL/CHECKs."""
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')
    contagem = {}
    for modelo, campo in VAZIO_PARA_NULO:
        n = apps.get_model(APP, modelo)._base_manager.filter(**{campo: ''}).update(**{campo: None})
        if n:
            contagem[f'{modelo}.{campo} vazio->NULL'] = n
    for modelo, campo in NULO_PARA_VAZIO:
        n = apps.get_model(APP, modelo)._base_manager.filter(**{f'{campo}__isnull': True}).update(**{campo: ''})
        if n:
            contagem[f'{modelo}.{campo} NULL->vazio'] = n
    for modelo in ATUALIZADO_EM_PELO_CRIADO_EM:
        n = (apps.get_model(APP, modelo)._base_manager.filter(atualizado_em__isnull=True)
             .update(atualizado_em=F('criado_em')))
        if n:
            contagem[f'{modelo}.atualizado_em NULL->criado_em'] = n
    for velha, nova in TABELA_NO_LOG.items():
        n = LogAuditoria.objects.filter(tabela=velha).update(tabela=nova)
        if n:
            contagem[f'LogAuditoria.tabela {velha}->{nova}'] = n
    if contagem:
        LogAuditoria.objects.create(
            acao='migration 0054: nulos e vazios normalizados (padrao SQL)', tabela='', detalhes=contagem,
        )
    if _pg(schema_editor):
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def desfazer_dados(apps, schema_editor):
    """Reverso: ''/NULL ficam (o NOT NULL e desfeito pelos AlterField); o alvo do log volta."""
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')
    for velha, nova in TABELA_NO_LOG.items():
        LogAuditoria.objects.filter(tabela=nova).update(tabela=velha)
    if _pg(schema_editor):
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def garantir_collation(apps, schema_editor):
    """pt_br (ICU, deterministica) — a 0038 cria; sem ICU ela so avisou: aqui aborta."""
    if not _pg(schema_editor):
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT to_regcollation('pt_br') IS NOT NULL")
        if cursor.fetchone()[0]:
            return
    try:
        with transaction.atomic(using=schema_editor.connection.alias):
            schema_editor.execute("CREATE COLLATION IF NOT EXISTS pt_br (provider = icu, locale = 'pt-BR')", None)
    except NotSupportedError as exc:
        raise RuntimeError(
            '0054 abortada: o Postgres nao tem ICU e a collation pt_br (cliente/profissional/procedimento.nome, '
            'spec padrao-sql §3.6.3) nao pode ser criada. Use um Postgres com ICU (Railway tem).'
        ) from exc


def criar_funcoes_triggers(apps, schema_editor):
    if _pg(schema_editor):
        _executar(schema_editor, SQL_FUNCOES)


def remover_funcoes_triggers(apps, schema_editor):
    if _pg(schema_editor):
        _executar(schema_editor, SQL_FUNCOES_REVERSO)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0053_comissao_ledger'),
    ]

    operations = [
        # ── 1. PG: triggers/funcoes da 0038/0046 saem antes dos renames ──
        migrations.RunPython(derrubar_triggers_legados, recriar_triggers_legados),
        # ── 2. tabelas (§3.1) + sequencias ──
        migrations.AlterModelTable(
            name='formularioanamnese',
            table='formulario',
        ),
        migrations.AlterModelTable(
            name='itempacote',
            table='pacote_item',
        ),
        migrations.AlterModelTable(
            name='respostaanamnese',
            table='resposta_formulario',
        ),
        migrations.RunPython(renomear_sequencias, desfazer_sequencias),
        # ── 3. colunas (§3.2): RENAME COLUMN, o atributo Python fica ──
        RenomearColuna(model_name='aceitetermo', name='conteudo_sha256', db_column='conteudo_hash'),
        RenomearColuna(model_name='aceitetermo', name='ip', db_column='ip_origem'),
        RenomearColuna(model_name='anotacaosessao', name='autor', db_column='usuario_autor_id'),
        RenomearColuna(model_name='anotacaosessao', name='autor_nome', db_column='usuario_autor_nome'),
        RenomearColuna(model_name='assinaturapush', name='ativo', db_column='eh_ativo'),
        RenomearColuna(model_name='assinaturapush', name='auth', db_column='chave_auth'),
        RenomearColuna(model_name='assinaturapush', name='p256dh', db_column='chave_p256dh'),
        RenomearColuna(model_name='assinaturapush', name='user', db_column='usuario_id'),
        RenomearColuna(model_name='atendimento', name='reagendado_de', db_column='atendimento_reagendado_id'),
        RenomearColuna(model_name='avaliacaonps', name='alerta_enviado', db_column='eh_alerta_enviado'),
        RenomearColuna(model_name='avaliacaonps', name='aprovado_publicacao', db_column='eh_publicacao_aprovada'),
        RenomearColuna(model_name='avaliacaonps', name='autoriza_publicacao', db_column='eh_publicacao_autorizada'),
        RenomearColuna(model_name='carteira', name='saldo', db_column='valor_saldo'),
        RenomearColuna(model_name='cliente', name='ativo', db_column='eh_ativo'),
        RenomearColuna(model_name='cliente', name='bloqueado_online', db_column='eh_bloqueado_online'),
        RenomearColuna(model_name='cliente', name='faltas_consecutivas', db_column='qtd_faltas_consecutivas'),
        RenomearColuna(model_name='cliente', name='foto_url', db_column='url_foto'),
        RenomearColuna(model_name='cliente', name='indicado_por', db_column='cliente_indicador_id'),
        RenomearColuna(model_name='codigootp', name='email', db_column='identificador'),
        RenomearColuna(model_name='codigootp', name='tentativas', db_column='qtd_tentativas'),
        RenomearColuna(model_name='formularioanamnese', name='ativo', db_column='eh_ativo'),
        RenomearColuna(model_name='formularioanamnese', name='obrigatorio', db_column='eh_obrigatorio'),
        RenomearColuna(model_name='formularioanamnese', name='schema_json', db_column='perguntas'),
        RenomearColuna(model_name='itempacote', name='quantidade_sessoes', db_column='qtd_sessoes'),
        RenomearColuna(model_name='listaespera', name='notificado', db_column='eh_notificado'),
        RenomearColuna(model_name='logauditoria', name='registro_id', db_column='registro_pk'),
        RenomearColuna(model_name='logauditoria', name='usuario', db_column='usuario_autor_id'),
        RenomearColuna(model_name='logauditoria', name='usuario_nome', db_column='usuario_autor_nome'),
        RenomearColuna(model_name='movimentocarteira', name='observacoes', db_column='observacao'),
        RenomearColuna(model_name='movimentocarteira', name='saldo_resultante', db_column='valor_saldo_resultante'),
        RenomearColuna(model_name='movimentocarteira', name='usuario', db_column='usuario_autor_id'),
        RenomearColuna(model_name='movimentocomissao', name='observacoes', db_column='observacao'),
        RenomearColuna(model_name='movimentocomissao', name='regra', db_column='regra_comissao_id'),
        RenomearColuna(model_name='pacote', name='ativo', db_column='eh_ativo'),
        RenomearColuna(model_name='pacote', name='preco_total', db_column='valor_total'),
        RenomearColuna(model_name='preco', name='vigente_desde', db_column='data_inicio_vigencia'),
        RenomearColuna(model_name='procedimento', name='ativo', db_column='eh_ativo'),
        RenomearColuna(model_name='procedimento', name='buffer_minutos', db_column='intervalo_minutos'),
        RenomearColuna(model_name='procedimento', name='exige_retorno', db_column='tem_retorno_obrigatorio'),
        RenomearColuna(model_name='procedimento', name='imagem_destaque', db_column='url_imagem_destaque'),
        RenomearColuna(model_name='procedimento', name='link_videoconferencia', db_column='url_videoconferencia'),
        RenomearColuna(model_name='profissional', name='ativo', db_column='eh_ativo'),
        RenomearColuna(model_name='profissional', name='bio', db_column='biografia'),
        RenomearColuna(model_name='profissional', name='foto_url', db_column='url_foto'),
        RenomearColuna(model_name='profissional', name='gcal_calendar_id', db_column='gcal_calendario_ref'),
        RenomearColuna(model_name='profissional', name='gcal_refresh_token', db_column='gcal_token_renovacao'),
        RenomearColuna(model_name='profissional', name='gcal_sync_token', db_column='gcal_token_sincronizacao'),
        RenomearColuna(model_name='profissional', name='gcal_ultimo_sync_em', db_column='gcal_sincronizado_em'),
        RenomearColuna(model_name='profissional', name='max_advance_dias', db_column='antecedencia_maxima_dias'),
        RenomearColuna(model_name='profissional', name='min_notice_horas', db_column='antecedencia_minima_horas'),
        RenomearColuna(model_name='promocao', name='ativa', db_column='eh_ativo'),
        RenomearColuna(model_name='promocao', name='desconto_percentual', db_column='percentual_desconto'),
        RenomearColuna(model_name='promocao', name='imagem_url', db_column='url_imagem'),
        RenomearColuna(model_name='promocao', name='preco_promocional', db_column='valor_promocional'),
        RenomearColuna(model_name='prontuario', name='respostas_extras', db_column='respostas'),
        RenomearColuna(model_name='prontuarioversao', name='autor', db_column='usuario_autor_id'),
        RenomearColuna(model_name='prontuarioversao', name='autor_nome', db_column='usuario_autor_nome'),
        RenomearColuna(model_name='regracomissao', name='ativo', db_column='eh_ativo'),
        RenomearColuna(model_name='regracomissao', name='valor', db_column='valor_fixo'),
        RenomearColuna(model_name='respostaanamnese', name='respondida_em', db_column='respondido_em'),
        RenomearColuna(model_name='respostaanamnese', name='respostas_json', db_column='respostas'),
        RenomearColuna(model_name='usuario', name='ativo', db_column='eh_ativo'),
        RenomearColuna(model_name='versaotermo', name='ativa', db_column='eh_ativo'),
        RenomearColuna(model_name='versaotermo', name='vigente_desde', db_column='data_inicio_vigencia'),
        # ── 4. tipos: registro_pk bigint, *_hash char(64) (conteudo_hash anulavel) ──
        migrations.AlterField(
            model_name='aceitetermo',
            name='conteudo_sha256',
            field=aranha_estetica.db.campos.HashSha256Field(blank=True, db_column='conteudo_hash', null=True),
        ),
        migrations.AlterField(
            model_name='codigootp',
            name='codigo_hash',
            field=aranha_estetica.db.campos.HashSha256Field(),
        ),
        migrations.AlterField(
            model_name='logauditoria',
            name='registro_id',
            field=models.BigIntegerField(blank=True, db_column='registro_pk', null=True),
        ),
        # ── 5. dados: ''<->NULL e atualizado_em ──
        migrations.RunPython(normalizar_dados, desfazer_dados),
        # ── 6. NOT NULL (DEFAULT '' / now()) ──
        migrations.AlterField(
            model_name='atendimento',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='NULL legado preenchido com criado_em na 0054.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='atendimento',
            name='descricao_preco',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='avaliacaonps',
            name='comentario',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='cliente',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='NULL legado preenchido com criado_em na 0054.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='configuracao',
            name='descricao',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='configuracao',
            name='valor',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='formularioanamnese',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='NULL legado preenchido com criado_em na 0054.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='logauditoria',
            name='tabela',
            field=models.CharField(blank=True, db_default='', default='', max_length=100),
        ),
        migrations.AlterField(
            model_name='notificacao',
            name='mensagem',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='pacote',
            name='descricao',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='preco',
            name='descricao',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='descricao',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='descricao_longa',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='imagem_destaque',
            field=models.URLField(blank=True, db_column='url_imagem_destaque', db_default='', default='', max_length=500),
        ),
        migrations.AlterField(
            model_name='profissional',
            name='especialidade',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='promocao',
            name='descricao',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='promocao',
            name='imagem_url',
            field=models.TextField(blank=True, db_column='url_imagem', db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='prontuario',
            name='alergias',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='prontuario',
            name='contraindicacoes',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='prontuario',
            name='historico_saude',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='prontuario',
            name='medicamentos_uso',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='prontuario',
            name='observacoes_gerais',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        # ── 7. criado_em/atualizado_em onde faltam (DEFAULT now()) ──
        migrations.AddField(
            model_name='assinaturapush',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='avaliacaonps',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='comprapacote',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='configuracao',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='configuracao',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_comment='Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='habilitacao',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_comment='Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='itempacote',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_comment='Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='listaespera',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='notificacao',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='pacote',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='pacote',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_comment='Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='preco',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='preco',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_comment='Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='procedimento',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='procedimento',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_comment='Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='profissional',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='profissional',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_comment='Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='promocao',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='promocao',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_comment='Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='prontuario',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_comment='Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='respostaanamnese',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='usuario',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='usuario',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_comment='Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='versaotermo',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_comment='Linhas anteriores a 0054 comecam com o instante da migration.', db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AddField(
            model_name='versaotermo',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_comment='Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).', db_default=django.db.models.functions.datetime.Now()),
        ),
        # ── 8. db_default (§1.2) ──
        migrations.AlterField(
            model_name='aceitetermo',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='aceitetermo',
            name='user_agent',
            field=models.CharField(blank=True, db_default='', default='', max_length=500),
        ),
        migrations.AlterField(
            model_name='anotacaosessao',
            name='autor_nome',
            field=models.CharField(blank=True, db_column='usuario_autor_nome', db_default='', default='', max_length=100),
        ),
        migrations.AlterField(
            model_name='anotacaosessao',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='assinaturapush',
            name='ativo',
            field=models.BooleanField(db_column='eh_ativo', db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name='assinaturapush',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='assinaturapush',
            name='user_agent',
            field=models.CharField(blank=True, db_default='', default='', max_length=300),
        ),
        migrations.AlterField(
            model_name='atendimento',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='atendimento',
            name='eh_retorno',
            field=models.BooleanField(db_default=False, db_index=True, default=False),
        ),
        migrations.AlterField(
            model_name='atendimento',
            name='status',
            field=models.CharField(choices=[('PENDENTE', 'Pendente de Confirmação'), ('AGENDADO', 'Agendado'), ('CONFIRMADO', 'Confirmado'), ('REALIZADO', 'Realizado'), ('CANCELADO', 'Cancelado'), ('FALTOU', 'Faltou'), ('REAGENDADO', 'Reagendado')], db_default='PENDENTE', default='PENDENTE', max_length=20),
        ),
        migrations.AlterField(
            model_name='avaliacaonps',
            name='alerta_enviado',
            field=models.BooleanField(db_column='eh_alerta_enviado', db_default=False, default=False),
        ),
        migrations.AlterField(
            model_name='avaliacaonps',
            name='aprovado_publicacao',
            field=models.BooleanField(db_column='eh_publicacao_aprovada', db_default=False, default=False),
        ),
        migrations.AlterField(
            model_name='avaliacaonps',
            name='autoriza_publicacao',
            field=models.BooleanField(db_column='eh_publicacao_autorizada', db_default=False, default=False),
        ),
        migrations.AlterField(
            model_name='avaliacaonps',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='carteira',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='carteira',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='carteira',
            name='saldo',
            field=models.DecimalField(db_column='valor_saldo', db_default=Decimal('0'), decimal_places=2, default=Decimal('0'), max_digits=10),
        ),
        migrations.AlterField(
            model_name='cliente',
            name='ativo',
            field=models.BooleanField(db_column='eh_ativo', db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name='cliente',
            name='bloqueado_online',
            field=models.BooleanField(db_column='eh_bloqueado_online', db_default=False, default=False),
        ),
        migrations.AlterField(
            model_name='cliente',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='cliente',
            name='faltas_consecutivas',
            field=models.SmallIntegerField(db_column='qtd_faltas_consecutivas', db_default=0, default=0, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='cliente',
            name='foto_url',
            field=models.URLField(blank=True, db_column='url_foto', db_default='', default='', help_text='URL S3 / storage', max_length=600),
        ),
        migrations.AlterField(
            model_name='codigootp',
            name='canal',
            field=models.CharField(choices=[('EMAIL', 'Email'), ('SMS', 'SMS')], db_default='SMS', default='SMS', max_length=10),
        ),
        migrations.AlterField(
            model_name='codigootp',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='codigootp',
            name='proposito',
            field=models.CharField(choices=[('AGENDAMENTO', 'Agendamento'), ('LOGIN_CLIENTE', 'Login cliente'), ('DSAR', 'Export de dados LGPD')], db_default='AGENDAMENTO', default='AGENDAMENTO', max_length=20),
        ),
        migrations.AlterField(
            model_name='comprapacote',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='comprapacote',
            name='status',
            field=models.CharField(choices=[('ATIVO', 'Ativo'), ('FINALIZADO', 'Finalizado'), ('CANCELADO', 'Cancelado'), ('EXPIRADO', 'Expirado')], db_default='ATIVO', default='ATIVO', max_length=20),
        ),
        migrations.AlterField(
            model_name='comprapacote',
            name='valor_reembolsado',
            field=models.DecimalField(db_default=0, decimal_places=2, default=0, max_digits=10, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='consumosessao',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='formularioanamnese',
            name='ativo',
            field=models.BooleanField(db_column='eh_ativo', db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name='formularioanamnese',
            name='categoria',
            field=models.CharField(blank=True, db_default='', default='', max_length=20),
        ),
        migrations.AlterField(
            model_name='formularioanamnese',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='formularioanamnese',
            name='escopo',
            field=models.CharField(choices=[('GLOBAL', 'Global (todo agendamento)'), ('CATEGORIA', 'Por categoria de procedimento'), ('PROCEDIMENTO', 'Por procedimento especifico'), ('MODALIDADE', 'Por modalidade (presencial/online/hibrido)')], db_default='GLOBAL', default='GLOBAL', max_length=20),
        ),
        migrations.AlterField(
            model_name='formularioanamnese',
            name='modalidade',
            field=models.CharField(blank=True, db_default='', default='', max_length=12),
        ),
        migrations.AlterField(
            model_name='formularioanamnese',
            name='obrigatorio',
            field=models.BooleanField(db_column='eh_obrigatorio', db_default=False, default=False),
        ),
        migrations.AlterField(
            model_name='formularioanamnese',
            name='schema_json',
            field=models.JSONField(db_column='perguntas', db_default=[], default=list),
        ),
        migrations.AlterField(
            model_name='formularioanamnese',
            name='tipo',
            field=models.CharField(choices=[('ANAMNESE', 'Anamnese pre-atendimento'), ('PESQUISA', 'Pesquisa pos-atendimento'), ('PRONTUARIO', 'Questionario do prontuario (equipe)')], db_default='ANAMNESE', db_index=True, default='ANAMNESE', max_length=12),
        ),
        migrations.AlterField(
            model_name='itempacote',
            name='quantidade_sessoes',
            field=models.SmallIntegerField(db_column='qtd_sessoes', db_default=1, default=1),
        ),
        migrations.AlterField(
            model_name='listaespera',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='listaespera',
            name='notificado',
            field=models.BooleanField(db_column='eh_notificado', db_default=False, default=False),
        ),
        migrations.AlterField(
            model_name='logauditoria',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='logauditoria',
            name='usuario_nome',
            field=models.CharField(blank=True, db_column='usuario_autor_nome', db_default='', default='', max_length=255),
        ),
        migrations.AlterField(
            model_name='movimentocarteira',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='movimentocarteira',
            name='observacoes',
            field=models.TextField(blank=True, db_column='observacao', db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='movimentocarteira',
            name='origem',
            field=models.CharField(choices=[('VALE_PRESENTE', 'Vale presente'), ('CANCELAMENTO', 'Refund cancelamento'), ('AJUSTE_MANUAL', 'Ajuste manual'), ('USO_AGENDAMENTO', 'Uso em agendamento'), ('CASHBACK_INDICACAO', 'Cashback por indicacao'), ('CASHBACK_ESTORNO', 'Estorno cashback'), ('OUTRO', 'Outro')], db_default='OUTRO', default='OUTRO', max_length=30),
        ),
        migrations.AlterField(
            model_name='movimentocomissao',
            name='observacoes',
            field=models.TextField(blank=True, db_column='observacao', db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='notificacao',
            name='canal',
            field=models.CharField(choices=[('WHATSAPP', 'WhatsApp'), ('SMS', 'SMS'), ('EMAIL', 'E-mail')], db_default='WHATSAPP', default='WHATSAPP', max_length=20),
        ),
        migrations.AlterField(
            model_name='notificacao',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='notificacao',
            name='status',
            field=models.CharField(choices=[('PENDENTE', 'Pendente'), ('ENVIADO', 'Enviado'), ('FALHOU', 'Falhou')], db_default='PENDENTE', default='PENDENTE', max_length=20),
        ),
        migrations.AlterField(
            model_name='notificacao',
            name='tipo',
            field=models.CharField(choices=[('LEMBRETE', 'Lembrete D-1'), ('LEMBRETE_2H', 'Lembrete T-2h'), ('CONFIRMACAO', 'Confirmação'), ('CANCELAMENTO', 'Cancelamento'), ('NPS', 'Pesquisa NPS'), ('PESQUISA', 'Pesquisa de satisfação detalhada'), ('APROVACAO', 'Aprovação Profissional'), ('TERMO', 'Termo de consentimento')], db_default='LEMBRETE', default='LEMBRETE', max_length=30),
        ),
        migrations.AlterField(
            model_name='pacote',
            name='ativo',
            field=models.BooleanField(db_column='eh_ativo', db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name='pacote',
            name='validade_meses',
            field=models.SmallIntegerField(db_default=12, default=12),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='ativo',
            field=models.BooleanField(db_column='eh_ativo', db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='buffer_minutos',
            field=models.SmallIntegerField(db_column='intervalo_minutos', db_default=0, default=0),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='categoria',
            field=models.CharField(choices=[('FACIAL', 'Facial'), ('CORPORAL', 'Corporal'), ('CAPILAR', 'Capilar'), ('OUTRO', 'Outro')], db_default='OUTRO', default='OUTRO', max_length=20),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='exige_retorno',
            field=models.BooleanField(db_column='tem_retorno_obrigatorio', db_default=False, default=False),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='link_videoconferencia',
            field=models.URLField(blank=True, db_column='url_videoconferencia', db_default='', default='', max_length=500),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='modalidade',
            field=models.CharField(choices=[('PRESENCIAL', 'Presencial'), ('ONLINE', 'Online'), ('HIBRIDO', 'Híbrido (presencial + online)')], db_default='PRESENCIAL', db_index=True, default='PRESENCIAL', max_length=12),
        ),
        migrations.AlterField(
            model_name='profissional',
            name='ativo',
            field=models.BooleanField(db_column='eh_ativo', db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name='profissional',
            name='bio',
            field=models.TextField(blank=True, db_column='biografia', db_default='', default='', help_text='Biografia publica do profissional'),
        ),
        migrations.AlterField(
            model_name='profissional',
            name='foto_url',
            field=models.URLField(blank=True, db_column='url_foto', db_default='', default='', max_length=600),
        ),
        migrations.AlterField(
            model_name='profissional',
            name='gcal_calendar_id',
            field=models.CharField(blank=True, db_column='gcal_calendario_ref', db_default='primary', default='primary', max_length=200),
        ),
        migrations.AlterField(
            model_name='profissional',
            name='gcal_refresh_token',
            field=models.TextField(blank=True, db_column='gcal_token_renovacao', db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='profissional',
            name='gcal_sync_token',
            field=models.TextField(blank=True, db_column='gcal_token_sincronizacao', db_default='', default=''),
        ),
        migrations.AlterField(
            model_name='profissional',
            name='max_advance_dias',
            field=models.SmallIntegerField(db_column='antecedencia_maxima_dias', db_default=60, default=60),
        ),
        migrations.AlterField(
            model_name='profissional',
            name='min_notice_horas',
            field=models.SmallIntegerField(db_column='antecedencia_minima_horas', db_default=2, default=2),
        ),
        migrations.AlterField(
            model_name='promocao',
            name='ativa',
            field=models.BooleanField(db_column='eh_ativo', db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name='promocao',
            name='desconto_percentual',
            field=models.DecimalField(db_column='percentual_desconto', db_default=0, decimal_places=2, default=0, max_digits=5),
        ),
        migrations.AlterField(
            model_name='prontuario',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='prontuario',
            name='respostas_extras',
            field=models.JSONField(blank=True, db_column='respostas', db_default={}, default=dict),
        ),
        migrations.AlterField(
            model_name='prontuarioversao',
            name='autor_nome',
            field=models.CharField(blank=True, db_column='usuario_autor_nome', db_default='', default='', max_length=100),
        ),
        migrations.AlterField(
            model_name='prontuarioversao',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='prontuarioversao',
            name='dados',
            field=models.JSONField(db_default={}, default=dict),
        ),
        migrations.AlterField(
            model_name='regracomissao',
            name='ativo',
            field=models.BooleanField(db_column='eh_ativo', db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name='regracomissao',
            name='atualizado_em',
            field=models.DateTimeField(auto_now=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='regracomissao',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='respostaanamnese',
            name='criado_em',
            field=models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now()),
        ),
        migrations.AlterField(
            model_name='respostaanamnese',
            name='respostas_json',
            field=models.JSONField(db_column='respostas', db_default={}, default=dict),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='ativo',
            field=models.BooleanField(db_column='eh_ativo', db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='papel',
            field=models.CharField(choices=[('ADMIN', 'Administrador'), ('PROFISSIONAL', 'Profissional'), ('RECEPCAO', 'Recepcao')], db_default='PROFISSIONAL', default='PROFISSIONAL', max_length=20),
        ),
        migrations.AlterField(
            model_name='versaotermo',
            name='ativa',
            field=models.BooleanField(db_column='eh_ativo', db_default=True, default=True),
        ),
        # ── 9. Positive*Field -> SmallIntegerField (CHECK nomeado na 0055) ──
        migrations.AlterField(
            model_name='codigootp',
            name='tentativas',
            field=models.SmallIntegerField(db_column='qtd_tentativas', db_default=0, default=0, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='duracao_retorno_minutos',
            field=models.SmallIntegerField(db_default=30, default=30, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='retorno_maximo_dias',
            field=models.SmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='retorno_minimo_dias',
            field=models.SmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        # ── 10. collation pt_br no estado (§3.6.3) ──
        migrations.RunPython(garantir_collation, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='cliente',
            name='nome',
            field=models.CharField(db_collation='pt_br', max_length=150),
        ),
        migrations.AlterField(
            model_name='procedimento',
            name='nome',
            field=models.CharField(db_collation='pt_br', max_length=100),
        ),
        migrations.AlterField(
            model_name='profissional',
            name='nome',
            field=models.CharField(db_collation='pt_br', max_length=100),
        ),
        # ── 11. PG: funcoes/triggers com nomes e colunas novos (§3.4, §5.2) ──
        migrations.RunPython(criar_funcoes_triggers, remover_funcoes_triggers),
    ]
