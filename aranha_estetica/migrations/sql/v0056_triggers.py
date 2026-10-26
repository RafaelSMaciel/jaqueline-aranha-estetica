"""0056 (L1) — triggers de atualizado_em, imutabilidade e trilha de dados (spec §5.2/§5.3).

CONGELADO: importado so pela 0056_logica_triggers_historico. Mudanca = modulo novo +
migration nova com CREATE OR REPLACE.
"""

# §5.2: toda tabela com atualizado_em (22) — o teste de conformidade reprova tabela com a
# coluna e sem tg_<t>_atualizado_em
TABELAS_ATUALIZADO_EM = (
    'usuario', 'assinatura_push', 'cliente', 'profissional', 'procedimento', 'preco', 'promocao',
    'agenda_horario', 'agenda_excecao', 'atendimento', 'notificacao', 'lista_espera', 'avaliacao_nps',
    'prontuario', 'formulario', 'resposta_formulario', 'versao_termo', 'pacote', 'compra_pacote',
    'carteira', 'regra_comissao', 'configuracao',
)

# §5.3: tabela -> (colunas ignoradas, colunas redigidas). cliente fica FORA de proposito (PII
# pura; o esquecimento obrigaria a expurgar a trilha); append-only e efemeras tambem.
TABELAS_HISTORICO = {
    'prontuario': ('atualizado_em', ''),
    'atendimento': ('atualizado_em', ''),
    'compra_pacote': ('atualizado_em', ''),
    'carteira': ('atualizado_em', ''),
    'regra_comissao': ('atualizado_em', ''),
    'preco': ('atualizado_em', ''),
    'promocao': ('atualizado_em', ''),
    'pacote': ('atualizado_em', ''),
    'versao_termo': ('atualizado_em', ''),
    'usuario': ('atualizado_em', 'senha_hash'),
    'configuracao': ('atualizado_em', ''),
}

FN_DEFINIR_ATUALIZADO_EM = """
CREATE OR REPLACE FUNCTION fn_definir_atualizado_em() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- O ORM (auto_now) ja manda o valor: so preenche quando o UPDATE nao tocou na coluna
    -- (QuerySet.update(), save(update_fields=...) sem ela, procedures, SQL manual). Assim o
    -- valor em memoria do Django nao diverge do banco (a trava otimista do prontuario compara
    -- atualizado_em).
    IF NEW.atualizado_em IS NOT DISTINCT FROM OLD.atualizado_em THEN
        NEW.atualizado_em := now();
    END IF;
    RETURN NEW;
END $$
"""

# = 0052/0054 (generica). CREATE OR REPLACE identico: nao muda nada se ja existe.
FN_BLOQUEAR_MUTACAO = """
CREATE OR REPLACE FUNCTION fn_bloquear_mutacao() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% e append-only: % bloqueado', TG_TABLE_NAME, TG_OP
        USING DETAIL = TG_TABLE_NAME || '.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
END $$
"""

TG_ANOTACAO_SESSAO_IMUTAVEL = (
    'CREATE TRIGGER tg_anotacao_sessao_imutavel BEFORE UPDATE OR DELETE ON anotacao_sessao '
    'FOR EACH ROW EXECUTE FUNCTION fn_bloquear_mutacao()'
)

CK_COLUNAS_ALTERADAS_LISTA = (
    'ALTER TABLE historico_alteracao ADD CONSTRAINT ck_historico_alteracao_colunas_alteradas_lista '
    "CHECK (jsonb_typeof(colunas_alteradas) = 'array')"
)

FN_HISTORICO_ALTERACAO_IMUTAVEL = """
CREATE OR REPLACE FUNCTION fn_historico_alteracao_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        -- so a purga de retencao (sp_purgar_dados_expirados liga a flag local da transacao)
        IF coalesce(current_setting('app.expurgo_autorizado', true), '') = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'historico_alteracao e trilha append-only: exclusao bloqueada'
            USING DETAIL = 'historico_alteracao.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.id, NEW.tabela, NEW.registro_pk, NEW.operacao, NEW.colunas_alteradas, NEW.dados_antes,
        NEW.dados_depois, NEW.ip_origem, NEW.origem, NEW.transacao_ref, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.tabela, OLD.registro_pk, OLD.operacao, OLD.colunas_alteradas, OLD.dados_antes,
        OLD.dados_depois, OLD.ip_origem, OLD.origem, OLD.transacao_ref, OLD.criado_em)
       OR (NEW.usuario_autor_id IS DISTINCT FROM OLD.usuario_autor_id AND NEW.usuario_autor_id IS NOT NULL) THEN
        RAISE EXCEPTION 'historico_alteracao e trilha append-only: registro imutavel'
            USING DETAIL = 'historico_alteracao.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;                                  -- so o SET NULL do autor passa
END $$
"""

TG_HISTORICO_ALTERACAO_IMUTAVEL = (
    'CREATE TRIGGER tg_historico_alteracao_imutavel BEFORE UPDATE OR DELETE ON historico_alteracao '
    'FOR EACH ROW EXECUTE FUNCTION fn_historico_alteracao_imutavel()'
)

FN_REGISTRAR_HISTORICO = """
CREATE OR REPLACE FUNCTION fn_registrar_historico() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_ignorar  text[] := CASE WHEN TG_NARGS > 0 AND TG_ARGV[0] <> '' THEN string_to_array(TG_ARGV[0], ',') ELSE '{}'::text[] END;
    v_redigir  text[] := CASE WHEN TG_NARGS > 1 AND TG_ARGV[1] <> '' THEN string_to_array(TG_ARGV[1], ',') ELSE '{}'::text[] END;
    v_antes    jsonb;
    v_depois   jsonb;
    v_colunas  jsonb := '[]'::jsonb;
    v_chave    text;
    v_pk       bigint;
    v_cfg      text;
    v_usuario  bigint;
    v_ip       inet;
BEGIN
    IF TG_OP = 'DELETE' THEN v_pk := OLD.id; ELSE v_pk := NEW.id; END IF;
    IF TG_OP <> 'INSERT' THEN v_antes  := to_jsonb(OLD) - v_ignorar; END IF;
    IF TG_OP <> 'DELETE' THEN v_depois := to_jsonb(NEW) - v_ignorar; END IF;

    IF TG_OP = 'UPDATE' THEN
        SELECT coalesce(jsonb_agg(d.key ORDER BY d.key), '[]'::jsonb)
          INTO v_colunas
          FROM jsonb_each(v_depois) AS d
         WHERE d.value IS DISTINCT FROM (v_antes -> d.key);
        IF v_colunas = '[]'::jsonb THEN
            RETURN NULL;                          -- nada relevante mudou (ex.: so atualizado_em)
        END IF;
        SELECT jsonb_object_agg(c.coluna, v_antes -> c.coluna), jsonb_object_agg(c.coluna, v_depois -> c.coluna)
          INTO v_antes, v_depois
          FROM jsonb_array_elements_text(v_colunas) AS c(coluna);   -- so o que mudou (menos PII duplicada)
    END IF;

    FOREACH v_chave IN ARRAY v_redigir LOOP                -- registra QUE mudou, nao o valor
        IF v_antes  ? v_chave THEN v_antes  := jsonb_set(v_antes,  ARRAY[v_chave], '"[redigido]"'); END IF;
        IF v_depois ? v_chave THEN v_depois := jsonb_set(v_depois, ARRAY[v_chave], '"[redigido]"'); END IF;
    END LOOP;

    -- contexto da aplicacao (ContextoBancoMiddleware / cron / Celery / migrate_atomico). A
    -- auditoria NUNCA pode derrubar a escrita de negocio: valor invalido/usuario inexistente vira
    -- NULL (a FK e deferida e so estouraria no COMMIT).
    v_cfg := current_setting('app.usuario_id', true);
    IF v_cfg ~ '^[0-9]{1,18}$' THEN
        v_usuario := v_cfg::bigint;
        PERFORM 1 FROM usuario WHERE id = v_usuario;
        IF NOT FOUND THEN v_usuario := NULL; END IF;
    END IF;
    BEGIN
        v_ip := NULLIF(current_setting('app.ip_origem', true), '')::inet;
    EXCEPTION WHEN invalid_text_representation THEN
        v_ip := NULL;
    END;

    INSERT INTO historico_alteracao
        (tabela, registro_pk, operacao, colunas_alteradas, dados_antes, dados_depois,
         usuario_autor_id, ip_origem, origem, transacao_ref)
    VALUES
        (TG_TABLE_NAME, v_pk, TG_OP, v_colunas, v_antes, v_depois,
         v_usuario, v_ip, left(coalesce(current_setting('app.origem', true), ''), 60),
         pg_current_xact_id()::text::bigint);
    RETURN NULL;                                  -- AFTER trigger
END $$
"""


def tg_atualizado_em(tabela):
    return (f'CREATE TRIGGER tg_{tabela}_atualizado_em BEFORE UPDATE ON {tabela} '
            'FOR EACH ROW EXECUTE FUNCTION fn_definir_atualizado_em()')


def tg_historico(tabela, ignorar, redigir):
    return (f'CREATE TRIGGER tg_{tabela}_historico AFTER INSERT OR UPDATE OR DELETE ON {tabela} '
            f"FOR EACH ROW EXECUTE FUNCTION fn_registrar_historico('{ignorar}', '{redigir}')")


CRIAR = [
    CK_COLUNAS_ALTERADAS_LISTA,
    FN_DEFINIR_ATUALIZADO_EM,
    *(tg_atualizado_em(t) for t in TABELAS_ATUALIZADO_EM),
    FN_BLOQUEAR_MUTACAO,
    TG_ANOTACAO_SESSAO_IMUTAVEL,
    FN_HISTORICO_ALTERACAO_IMUTAVEL,
    TG_HISTORICO_ALTERACAO_IMUTAVEL,
    FN_REGISTRAR_HISTORICO,
    *(tg_historico(t, ign, red) for t, (ign, red) in TABELAS_HISTORICO.items()),
]

REMOVER = [
    *(f'DROP TRIGGER IF EXISTS tg_{t}_historico ON {t}' for t in TABELAS_HISTORICO),
    'DROP FUNCTION IF EXISTS fn_registrar_historico()',
    'DROP TRIGGER IF EXISTS tg_historico_alteracao_imutavel ON historico_alteracao',
    'DROP FUNCTION IF EXISTS fn_historico_alteracao_imutavel()',
    'DROP TRIGGER IF EXISTS tg_anotacao_sessao_imutavel ON anotacao_sessao',
    # fn_bloquear_mutacao e da 0052 (compra_pacote_item, movimento_*): fica
    *(f'DROP TRIGGER IF EXISTS tg_{t}_atualizado_em ON {t}' for t in TABELAS_ATUALIZADO_EM),
    'DROP FUNCTION IF EXISTS fn_definir_atualizado_em()',
    'ALTER TABLE historico_alteracao DROP CONSTRAINT IF EXISTS ck_historico_alteracao_colunas_alteradas_lista',
]
