"""0058 (L3) — procedures: fonte UNICA da escrita de retencao/LGPD no Postgres (spec §5.6).

CONGELADO: importado so pela 0058_logica_procedures. Mudanca = modulo novo + migration nova
com CREATE OR REPLACE.

O Python chama por utils/banco.chamar() quando usa_logica_pg() (Postgres); o fallback Python
(LgpdService._esquecer_cliente_python, purgar_*, tasks_manutencao._purgar_*, o update() de
job_expirar_pacotes) existe so para o SQLite dos testes e para o teste de paridade.

Regras: SET search_path = public, pg_catalog em todas, EXCETO sp_aplicar_retencao_lgpd, que da
COMMIT por cliente (o PG proibe controle de transacao em rotina com SET) — essa qualifica tudo com
public. Erros de regra: SQLSTATE de classe 23 (IntegrityError no Django).
"""

SP_EXPIRAR_PACOTES = """
CREATE OR REPLACE PROCEDURE sp_expirar_pacotes(
    IN    p_hoje          date    DEFAULT NULL,
    INOUT qtd_expirados   integer DEFAULT NULL)
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
BEGIN
    UPDATE compra_pacote
       SET status = 'EXPIRADO'
     WHERE status = 'ATIVO'
       AND data_expiracao < coalesce(p_hoje, fn_data_local(now()));   -- o ultimo dia ainda vale
    GET DIAGNOSTICS qtd_expirados = ROW_COUNT;
END $$
"""

# Espelho executavel de LgpdService.esquecer_cliente (direito ao esquecimento). Os passos e a
# ordem sao os do Python (_esquecer_cliente_python), comparados por "foto" no teste de paridade.
SP_ANONIMIZAR_CLIENTE = """
CREATE OR REPLACE PROCEDURE sp_anonimizar_cliente(
    IN p_cliente_id        bigint,
    IN p_usuario_autor_id  bigint DEFAULT NULL,
    IN p_ip_origem         inet   DEFAULT NULL)
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_cli         cliente%ROWTYPE;
    v_pseudo      text := '[ANONIMIZADO-' || p_cliente_id || ']';
    -- relogio real, nao o inicio da transacao: a revogacao precisa ser POSTERIOR a qualquer
    -- consentimento ja gravado (o Python grava registrado_em com o relogio da aplicacao)
    v_agora       timestamptz := clock_timestamp();
    v_atend_ids   bigint[];
    v_compra_ids  bigint[];
    v_espera_ids  bigint[];
    v_pront_ids   bigint[];
    v_nps_ids     bigint[];
    v_notif_ids   bigint[];
    v_cart_ids    bigint[];
    v_autor_nome  text := '';
BEGIN
    -- trava a linha do titular: serializa com um agendamento simultaneo do mesmo cliente
    SELECT * INTO v_cli FROM cliente WHERE id = p_cliente_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_anonimizar_cliente: cliente % inexistente', p_cliente_id USING ERRCODE = 'no_data_found';
    END IF;

    -- ids antes de apagar (pseudonimizacao do django_admin_log)
    SELECT coalesce(array_agg(id), '{}') INTO v_atend_ids  FROM atendimento   WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(id), '{}') INTO v_compra_ids FROM compra_pacote WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(id), '{}') INTO v_espera_ids FROM lista_espera  WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(id), '{}') INTO v_pront_ids  FROM prontuario    WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(id), '{}') INTO v_cart_ids   FROM carteira      WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(n.id), '{}') INTO v_nps_ids
      FROM avaliacao_nps n WHERE n.atendimento_id = ANY (v_atend_ids);
    SELECT coalesce(array_agg(n.id), '{}') INTO v_notif_ids
      FROM notificacao n WHERE n.atendimento_id = ANY (v_atend_ids);

    -- 1. cadastro (tg_cliente_atualizado_em preenche atualizado_em)
    UPDATE cliente
       SET nome = v_pseudo, cpf = NULL, rg = NULL, email = NULL, telefone = NULL, cep = NULL,
           endereco = NULL, profissao = NULL, data_nascimento = NULL, url_foto = '',
           eh_ativo = false, deletado_em = coalesce(deletado_em, v_agora)
     WHERE id = p_cliente_id;

    -- 2. consentimentos: revoga o que esta concedido (historico fica) e minimiza IP/user-agent
    -- (registrado_em >= o ultimo do par + 1 us: "o mais recente manda" mesmo com relogio adiantado)
    INSERT INTO consentimento (cliente_id, finalidade, canal, eh_concedido, origem, texto, registrado_em)
    SELECT a.cliente_id, a.finalidade, a.canal, false, 'ESQUECIMENTO',
           'Revogacao automatica (direito ao esquecimento)',
           greatest(v_agora, a.registrado_em + interval '1 microsecond')
      FROM vw_consentimento_atual a
     WHERE a.cliente_id = p_cliente_id AND a.eh_concedido;
    UPDATE consentimento SET ip_origem = NULL, user_agent = ''
     WHERE cliente_id = p_cliente_id AND (ip_origem IS NOT NULL OR user_agent <> '');

    -- 3. links publicos do titular deixam de funcionar (revogado = 404)
    UPDATE link_acesso SET revogado_em = v_agora
     WHERE revogado_em IS NULL
       AND (cliente_id = p_cliente_id
            OR atendimento_id = ANY (v_atend_ids)
            OR notificacao_id = ANY (v_notif_ids)
            OR resposta_formulario_id IN (SELECT id FROM resposta_formulario WHERE cliente_id = p_cliente_id));

    -- 4. OTP (telefone, pseudo-e-mail do fluxo so-telefone, e-mail)
    DELETE FROM codigo_otp
     WHERE (v_cli.telefone IS NOT NULL
            AND (telefone = v_cli.telefone OR identificador = 'sms+' || v_cli.telefone || '@shivazen.local'))
        OR (v_cli.email IS NOT NULL AND upper(identificador) = upper(v_cli.email));

    -- 5. lista de espera e texto das notificacoes (nome do cliente)
    DELETE FROM lista_espera WHERE cliente_id = p_cliente_id;
    UPDATE notificacao SET mensagem = '' WHERE id = ANY (v_notif_ids) AND mensagem <> '';

    -- 6. dado de saude sem atendimento REALIZADO: fim da finalidade (a cascata apaga o link_acesso)
    DELETE FROM resposta_formulario r
     WHERE r.cliente_id = p_cliente_id
       AND NOT EXISTS (SELECT 1 FROM atendimento a WHERE a.id = r.atendimento_id AND a.status = 'REALIZADO');

    -- 7. depoimento sai do site e perde o texto livre (a nota fica p/ estatistica)
    UPDATE avaliacao_nps
       SET eh_publicacao_autorizada = false, eh_publicacao_aprovada = false, comentario = ''
     WHERE id = ANY (v_nps_ids);

    -- 8. trilha de acoes: o nome do titular vira o pseudonimo (autor, data, tabela, id ficam)
    IF length(btrim(coalesce(v_cli.nome, ''))) >= 3 AND v_cli.nome NOT LIKE '[ANONIMIZADO-%' THEN
        UPDATE log_auditoria SET acao = replace(acao, v_cli.nome, v_pseudo)
         WHERE strpos(acao, v_cli.nome) > 0
           AND ((tabela = 'cliente'       AND registro_pk = p_cliente_id)
             OR (tabela = 'prontuario'    AND registro_pk = ANY (array_append(v_pront_ids, p_cliente_id)))
             OR (tabela = 'atendimento'   AND registro_pk = ANY (v_atend_ids))
             OR (tabela = 'compra_pacote' AND registro_pk = ANY (v_compra_ids)));
    END IF;
    UPDATE log_auditoria SET acao = 'Editou cliente: ' || v_pseudo
     WHERE tabela = 'cliente' AND registro_pk = p_cliente_id AND acao LIKE 'Editou cliente:%';
    UPDATE log_auditoria SET detalhes = NULL
     WHERE tabela = 'cliente' AND registro_pk = p_cliente_id AND acao LIKE 'migration 0034%';

    -- 9. historico do Django admin (object_repr = str(obj) = nome)
    UPDATE django_admin_log l SET object_repr = v_pseudo
      FROM django_content_type ct
     WHERE ct.id = l.content_type_id AND ct.app_label = 'aranha_estetica'
       AND ((ct.model = 'cliente'       AND l.object_id = p_cliente_id::text)
         OR (ct.model = 'atendimento'   AND l.object_id = ANY (v_atend_ids::text[]))
         OR (ct.model = 'comprapacote'  AND l.object_id = ANY (v_compra_ids::text[]))
         OR (ct.model = 'listaespera'   AND l.object_id = ANY (v_espera_ids::text[]))
         OR (ct.model = 'avaliacaonps'  AND l.object_id = ANY (v_nps_ids::text[]))
         OR (ct.model = 'notificacao'   AND l.object_id = ANY (v_notif_ids::text[]))
         OR (ct.model = 'carteira'      AND l.object_id = ANY (v_cart_ids::text[])));

    -- 10. registro LGPD (sem PII): autor + snapshot "Nome <email>" (= models.sistema.rotulo_usuario)
    SELECT left(CASE WHEN btrim(u.nome) <> '' AND btrim(u.email) <> '' THEN btrim(u.nome) || ' <' || btrim(u.email) || '>'
                     ELSE coalesce(nullif(btrim(u.nome), ''), btrim(u.email)) END, 255)
      INTO v_autor_nome FROM usuario u WHERE u.id = p_usuario_autor_id;
    INSERT INTO log_auditoria (usuario_autor_id, usuario_autor_nome, acao, tabela, registro_pk, ip_origem)
    VALUES (p_usuario_autor_id, coalesce(v_autor_nome, ''),
            'Cliente anonimizado (direito ao esquecimento / retencao LGPD)', 'cliente', p_cliente_id, p_ip_origem);
END $$
"""

# Espelho de LgpdService.purgar_inativos + purgar_fichas_sem_atendimento + purgar_lista_espera_vencida
SP_APLICAR_RETENCAO_LGPD = """
CREATE OR REPLACE PROCEDURE sp_aplicar_retencao_lgpd(
    IN    p_retencao_cliente_dias  integer,
    IN    p_carencia_soft_dias     integer,
    IN    p_retencao_saude_dias    integer,
    IN    p_retencao_ficha_dias    integer,
    IN    p_confirmar_por_cliente  boolean DEFAULT true,
    INOUT qtd_clientes             integer DEFAULT NULL,
    INOUT qtd_fichas               integer DEFAULT NULL,
    INOUT qtd_esperas              integer DEFAULT NULL)
LANGUAGE plpgsql AS $$                -- sem SET search_path: rotina com SET nao pode dar COMMIT
DECLARE
    v_id    bigint;
    v_agora timestamptz := now();
BEGIN
    qtd_clientes := 0;
    FOR v_id IN SELECT public.fn_clientes_para_anonimizar(p_retencao_cliente_dias, p_carencia_soft_dias,
                                                           p_retencao_saude_dias, v_agora)
    LOOP
        CALL public.sp_anonimizar_cliente(v_id, NULL, NULL);
        qtd_clientes := qtd_clientes + 1;
        IF p_confirmar_por_cliente THEN
            COMMIT;                   -- como o laco Python: um aborto no meio preserva os ja feitos
        END IF;
    END LOOP;

    -- fichas de pedido nao realizado (espelho de LgpdService.fichas_sem_atendimento_para_purga)
    DELETE FROM public.resposta_formulario r
     WHERE r.criado_em < v_agora - make_interval(days => p_retencao_ficha_dias)
       AND (EXISTS (SELECT 1 FROM public.atendimento a WHERE a.id = r.atendimento_id AND a.status = 'CANCELADO')
            OR EXISTS (SELECT 1 FROM public.atendimento a WHERE a.id = r.atendimento_id
                          AND a.status = 'PENDENTE' AND a.data_hora_inicio < v_agora)
            OR (r.atendimento_id IS NULL AND r.respondido_em IS NULL)
            OR (r.respondido_em IS NULL AND r.respostas = '{}'::jsonb
                AND EXISTS (SELECT 1 FROM public.atendimento a WHERE a.id = r.atendimento_id
                               AND a.data_hora_inicio < v_agora)));
    GET DIAGNOSTICS qtd_fichas = ROW_COUNT;
    IF qtd_fichas > 0 THEN
        INSERT INTO public.log_auditoria (acao, tabela, detalhes)
        VALUES ('Purga de fichas de pedido nao realizado', 'resposta_formulario', jsonb_build_object('qtd', qtd_fichas));
    END IF;

    DELETE FROM public.lista_espera WHERE data_desejada < public.fn_data_local(v_agora);
    GET DIAGNOSTICS qtd_esperas = ROW_COUNT;
    IF qtd_esperas > 0 THEN
        INSERT INTO public.log_auditoria (acao, tabela, detalhes)
        VALUES ('Purga da lista de espera vencida', 'lista_espera', jsonb_build_object('qtd', qtd_esperas));
    END IF;
END $$
"""

# Espelho de tasks_manutencao._purgar_otp/_limpar_texto_notificacoes/_purgar_auditoria/_purgar_links/
# _purgar_historico (etapa "banco" do job_housekeeping)
SP_PURGAR_DADOS_EXPIRADOS = """
CREATE OR REPLACE PROCEDURE sp_purgar_dados_expirados(
    IN    p_retencao_otp_horas         integer,
    IN    p_retencao_notificacao_dias  integer,
    IN    p_retencao_log_dias          integer,
    IN    p_retencao_link_dias         integer DEFAULT 90,
    INOUT qtd_otp                      integer DEFAULT NULL,
    INOUT qtd_notificacoes             integer DEFAULT NULL,
    INOUT qtd_logs                     integer DEFAULT NULL,
    INOUT qtd_links                    integer DEFAULT NULL,
    INOUT qtd_historico                integer DEFAULT NULL,
    INOUT etapas_com_falha             text[]  DEFAULT NULL)
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_agora timestamptz := now();
BEGIN
    etapas_com_falha := '{}';
    -- cada etapa num sub-bloco (subtransacao): falha de uma nao impede as outras (= housekeeping)
    BEGIN
        DELETE FROM codigo_otp WHERE criado_em < v_agora - make_interval(hours => p_retencao_otp_horas);
        GET DIAGNOSTICS qtd_otp = ROW_COUNT;
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'otp'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/otp: %', SQLERRM;
    END;
    BEGIN
        UPDATE notificacao SET mensagem = ''
         WHERE criado_em < v_agora - make_interval(days => p_retencao_notificacao_dias) AND mensagem <> '';
        GET DIAGNOSTICS qtd_notificacoes = ROW_COUNT;
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'notificacoes'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/notificacoes: %', SQLERRM;
    END;
    BEGIN
        DELETE FROM log_auditoria WHERE criado_em < v_agora - make_interval(days => p_retencao_log_dias);
        GET DIAGNOSTICS qtd_logs = ROW_COUNT;
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'auditoria'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/auditoria: %', SQLERRM;
    END;
    BEGIN
        -- vencido ou revogado ha mais de N dias (link sem validade e nao revogado fica: feed ICS)
        DELETE FROM link_acesso
         WHERE coalesce(revogado_em, expira_em) < v_agora - make_interval(days => p_retencao_link_dias);
        GET DIAGNOSTICS qtd_links = ROW_COUNT;
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'links'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/links: %', SQLERRM;
    END;
    BEGIN
        PERFORM set_config('app.expurgo_autorizado', 'on', true);       -- local a transacao
        DELETE FROM historico_alteracao
         WHERE tabela <> 'prontuario'                                     -- clinico: 20 anos
           AND criado_em < v_agora - make_interval(days => p_retencao_log_dias);
        GET DIAGNOSTICS qtd_historico = ROW_COUNT;
        PERFORM set_config('app.expurgo_autorizado', 'off', true);
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'historico'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/historico: %', SQLERRM;
    END;
END $$
"""

PROCEDURES = (
    ('sp_expirar_pacotes', 'date, integer', SP_EXPIRAR_PACOTES),
    ('sp_anonimizar_cliente', 'bigint, bigint, inet', SP_ANONIMIZAR_CLIENTE),
    ('sp_aplicar_retencao_lgpd', 'integer, integer, integer, integer, boolean, integer, integer, integer',
     SP_APLICAR_RETENCAO_LGPD),
    ('sp_purgar_dados_expirados',
     'integer, integer, integer, integer, integer, integer, integer, integer, integer, text[]',
     SP_PURGAR_DADOS_EXPIRADOS),
)

CRIAR = [sql for _nome, _args, sql in PROCEDURES]
REMOVER = [f'DROP PROCEDURE IF EXISTS {nome}({args})' for nome, args, _sql in reversed(PROCEDURES)]
