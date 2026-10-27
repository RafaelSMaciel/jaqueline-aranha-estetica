"""0059 — trilhas append-only de verdade: log_auditoria imutavel e expurgo so por idade.

CONGELADO: importado so pela 0059_trilha_append_only. Mudanca = modulo novo + migration nova
com CREATE OR REPLACE.

Ate a 0058 so o Django admin era somente leitura em log_auditoria (leituras de prontuario,
DSAR, 2FA) e historico_alteracao aceitava qualquer DELETE da sessao que ligasse
app.expurgo_autorizado. Com o mesmo usuario do banco para app e migrations, um SQL injetado
apagava a prova (LGPD art. 37/46). Agora o banco so aceita:

- log_auditoria: DELETE de linha com 180+ dias (purga de retencao, RETENCAO_LOG_AUDITORIA_DIAS
  >= 180 — aranha.W013) e UPDATE da pseudonimizacao (acao passa a conter o pseudonimo
  "[ANONIMIZADO-<id>]", detalhes vira NULL) e do SET NULL do autor;
- historico_alteracao: DELETE com a flag E idade minima (180 dias; prontuario 20 anos, o
  registro clinico, que a purga nem tenta apagar).

180 dias = TRILHA_RETENCAO_MINIMA_DIAS (constants.py; o teste confere que os dois batem).
"""

FN_LOG_AUDITORIA_IMUTAVEL = """
CREATE OR REPLACE FUNCTION fn_log_auditoria_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.criado_em < now() - interval '180 days' THEN      -- purga de retencao
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'log_auditoria e trilha append-only: so a purga apaga linha com 180+ dias'
            USING DETAIL = 'log_auditoria.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.id, NEW.usuario_autor_nome, NEW.tabela, NEW.registro_pk, NEW.ip_origem, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.usuario_autor_nome, OLD.tabela, OLD.registro_pk, OLD.ip_origem, OLD.criado_em)
       OR (NEW.usuario_autor_id IS DISTINCT FROM OLD.usuario_autor_id AND NEW.usuario_autor_id IS NOT NULL)
       OR (NEW.acao IS DISTINCT FROM OLD.acao AND strpos(NEW.acao, '[ANONIMIZADO-') = 0)
       OR (NEW.detalhes IS DISTINCT FROM OLD.detalhes AND NEW.detalhes IS NOT NULL) THEN
        RAISE EXCEPTION 'log_auditoria e trilha append-only: registro imutavel'
            USING DETAIL = 'log_auditoria.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;            -- so pseudonimizacao (esquecimento) e SET NULL do autor passam
END $$
"""

TG_LOG_AUDITORIA_IMUTAVEL = (
    'CREATE TRIGGER tg_log_auditoria_imutavel BEFORE UPDATE OR DELETE ON log_auditoria '
    'FOR EACH ROW EXECUTE FUNCTION fn_log_auditoria_imutavel()'
)

# = v0056 + idade minima no DELETE
FN_HISTORICO_ALTERACAO_IMUTAVEL = """
CREATE OR REPLACE FUNCTION fn_historico_alteracao_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        -- so a purga de retencao (sp_purgar_dados_expirados liga a flag local da transacao) e
        -- so do que ja passou da idade minima: a flag sozinha nao apaga trilha recente
        IF coalesce(current_setting('app.expurgo_autorizado', true), '') = 'on'
           -- CASE entre parenteses: sem eles o PL/pgSQL toma o THEN do CASE como o do IF
           AND OLD.criado_em < now() - (CASE WHEN OLD.tabela = 'prontuario'
                                             THEN interval '20 years' ELSE interval '180 days' END) THEN
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

CRIAR = [
    FN_LOG_AUDITORIA_IMUTAVEL,
    TG_LOG_AUDITORIA_IMUTAVEL,
    FN_HISTORICO_ALTERACAO_IMUTAVEL,
]
