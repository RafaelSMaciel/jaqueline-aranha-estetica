"""0077 — retencao LGPD de verdade (decisoes 33 e 34): saude por janela de 20 anos, pacote 5 anos
depois de expirar, expurgo de saude e pseudonimizacao sem vinculos que reidentificam.

CONGELADO: importado so pela 0077_retencao_saude_pacote (e pelos testes que conferem a versao
vigente). Mudanca = modulo novo + migration nova com CREATE OR REPLACE.

O que muda (os prazos vem de settings.RETENCAO_*, passados por services/lgpd e jobs_manutencao):

- fn_clientes_para_anonimizar ganha p_retencao_pacote_dias: o registro de saude (atendimento
  REALIZADO, versao do prontuario, prontuario com conteudo editado) retem so dentro da janela de
  p_retencao_saude_dias, nao para sempre; pacote retem ate p_retencao_pacote_dias depois de
  coalesce(cancelado_em, data_expiracao, criado_em). Antes: quem tinha ficha ou pacote ficava
  identificada para sempre (contra os arts. 15/16 e a politica publicada).
- sp_aplicar_retencao_lgpd passa o prazo do pacote adiante (9 argumentos).
- sp_purgar_dados_expirados ganha a etapa "saude" (12 argumentos): expurga o registro de saude de
  quem teve o ultimo evento de saude (REALIZADO, edicao/versao do prontuario, anotacao, ficha) ha
  mais de p_retencao_saude_dias — prontuario_versao, anotacao_sessao, resposta_formulario, o
  prontuario e a trilha historico_alteracao do prontuario — e minimiza IP/user-agent dos aceites
  SAUDE e PROCEDIMENTO dessa cliente. Roda com app.expurgo_autorizado (local a transacao).
- triggers: prontuario_versao e anotacao_sessao aceitam DELETE so com a flag E 20 anos de idade
  (nunca recente, nem com a flag); aceite_termo aceita so a minimizacao (IP -> NULL, user-agent ->
  ''); fn_registrar_historico nao copia para a trilha o prontuario expurgado (mesma flag + idade).
- sp_anonimizar_cliente (pseudonimizacao): zera codigo_indicacao e cliente_indicador_id, minimiza
  IP/user-agent do aceite LGPD (e dos aceites SAUDE/PROCEDIMENTO quando nao ha mais registro de
  saude), apaga a inscricao anonima da lista de espera pelo telefone (0076) e limpa os detalhes das
  trilhas 'LGPD: ...' da titular. O resto e o da sql/v0074_anonimizar_cliente.

20 anos nos triggers e o minimo legal (registro de saude): a procedure recebe o prazo do settings,
e um prazo menor que 20 anos faz o trigger recusar e a etapa "saude" falhar (etapas_com_falha).
"""

FN_CLIENTES_PARA_ANONIMIZAR = """
CREATE OR REPLACE FUNCTION fn_clientes_para_anonimizar(
    p_retencao_cliente_dias integer, p_carencia_soft_dias integer, p_retencao_saude_dias integer,
    p_retencao_pacote_dias integer, p_agora timestamptz DEFAULT now())
RETURNS SETOF bigint
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT c.id FROM cliente c
     WHERE ((c.deletado_em IS NULL AND c.criado_em < p_agora - make_interval(days => p_retencao_cliente_dias))
            OR c.deletado_em < p_agora - make_interval(days => p_carencia_soft_dias))
       AND c.nome NOT LIKE '[ANONIMIZADO-%'
       AND NOT EXISTS (SELECT 1 FROM atendimento a WHERE a.cliente_id = c.id
                          AND a.data_hora_inicio >= p_agora - make_interval(days => p_retencao_cliente_dias))
       -- registro de saude: retem dentro da janela (ultimo REALIZADO, ultima versao, ultima edicao)
       AND NOT EXISTS (SELECT 1 FROM atendimento a WHERE a.cliente_id = c.id AND a.status = 'REALIZADO'
                          AND a.data_hora_inicio >= p_agora - make_interval(days => p_retencao_saude_dias))
       AND NOT EXISTS (SELECT 1 FROM prontuario p WHERE p.cliente_id = c.id
                          AND p.atualizado_em >= p_agora - make_interval(days => p_retencao_saude_dias)
                          AND (p.alergias <> '' OR p.contraindicacoes <> '' OR p.historico_saude <> ''
                               OR p.medicamentos_uso <> '' OR p.observacoes_gerais <> '' OR p.respostas <> '{}'::jsonb))
       AND NOT EXISTS (SELECT 1 FROM prontuario_versao v JOIN prontuario p ON p.id = v.prontuario_id
                        WHERE p.cliente_id = c.id
                          AND v.criado_em >= p_agora - make_interval(days => p_retencao_saude_dias))
       -- pacote e contrato: retem ate N dias depois de cancelar/expirar (sem validade: da compra)
       AND NOT EXISTS (SELECT 1 FROM compra_pacote cp WHERE cp.cliente_id = c.id
                          AND coalesce(fn_data_local(cp.cancelado_em), cp.data_expiracao, fn_data_local(cp.criado_em))
                              >= fn_data_local(p_agora - make_interval(days => p_retencao_pacote_dias)))
     ORDER BY c.id;
END
"""

SP_APLICAR_RETENCAO_LGPD = """
CREATE OR REPLACE PROCEDURE sp_aplicar_retencao_lgpd(
    IN    p_retencao_cliente_dias  integer,
    IN    p_carencia_soft_dias     integer,
    IN    p_retencao_saude_dias    integer,
    IN    p_retencao_ficha_dias    integer,
    IN    p_retencao_pacote_dias   integer,
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
                                                           p_retencao_saude_dias, p_retencao_pacote_dias, v_agora)
    LOOP
        CALL public.sp_anonimizar_cliente(v_id, NULL, NULL);
        qtd_clientes := qtd_clientes + 1;
        IF p_confirmar_por_cliente THEN
            COMMIT;                   -- um aborto no meio preserva os ja feitos
        END IF;
    END LOOP;

    -- fichas de pedido nao realizado (fim da finalidade)
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

    -- lista de espera vencida (cliente verificada ou inscricao anonima)
    DELETE FROM public.lista_espera WHERE data_desejada < public.fn_data_local(v_agora);
    GET DIAGNOSTICS qtd_esperas = ROW_COUNT;
    IF qtd_esperas > 0 THEN
        INSERT INTO public.log_auditoria (acao, tabela, detalhes)
        VALUES ('Purga da lista de espera vencida', 'lista_espera', jsonb_build_object('qtd', qtd_esperas));
    END IF;
END $$
"""

SP_PURGAR_DADOS_EXPIRADOS = """
CREATE OR REPLACE PROCEDURE sp_purgar_dados_expirados(
    IN    p_retencao_otp_horas         integer,
    IN    p_retencao_notificacao_dias  integer,
    IN    p_retencao_log_dias          integer,
    IN    p_retencao_link_dias         integer DEFAULT 90,
    IN    p_retencao_saude_dias        integer DEFAULT 7300,
    INOUT qtd_otp                      integer DEFAULT NULL,
    INOUT qtd_notificacoes             integer DEFAULT NULL,
    INOUT qtd_logs                     integer DEFAULT NULL,
    INOUT qtd_links                    integer DEFAULT NULL,
    INOUT qtd_historico                integer DEFAULT NULL,
    INOUT qtd_saude                    integer DEFAULT NULL,
    INOUT etapas_com_falha             text[]  DEFAULT NULL)
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_agora   timestamptz := now();
    v_limite  timestamptz;
    v_ids     bigint[];
    v_pront   bigint[];
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
         WHERE tabela <> 'prontuario'                                     -- clinico: etapa saude
           AND criado_em < v_agora - make_interval(days => p_retencao_log_dias);
        GET DIAGNOSTICS qtd_historico = ROW_COUNT;
        PERFORM set_config('app.expurgo_autorizado', 'off', true);
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'historico'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/historico: %', SQLERRM;
    END;
    BEGIN
        -- registro de saude: expurgo depois de p_retencao_saude_dias do ULTIMO evento de saude da
        -- cliente (REALIZADO, edicao/versao do prontuario, anotacao, ficha), so de quem tem registro
        v_limite := v_agora - make_interval(days => p_retencao_saude_dias);
        SELECT coalesce(array_agg(e.cliente_id), '{}') INTO v_ids
          FROM (SELECT cliente_id, max(em) AS ultimo FROM (
                    SELECT a.cliente_id, a.data_hora_inicio AS em FROM atendimento a WHERE a.status = 'REALIZADO'
                    UNION ALL SELECT p.cliente_id, p.atualizado_em FROM prontuario p
                    UNION ALL SELECT p.cliente_id, v.criado_em
                                FROM prontuario_versao v JOIN prontuario p ON p.id = v.prontuario_id
                    UNION ALL SELECT a.cliente_id, n.criado_em
                                FROM anotacao_sessao n JOIN atendimento a ON a.id = n.atendimento_id
                    UNION ALL SELECT r.cliente_id, r.criado_em FROM resposta_formulario r
                ) eventos GROUP BY cliente_id) e
         WHERE e.ultimo < v_limite
           AND (EXISTS (SELECT 1 FROM prontuario p WHERE p.cliente_id = e.cliente_id)
                OR EXISTS (SELECT 1 FROM resposta_formulario r WHERE r.cliente_id = e.cliente_id)
                OR EXISTS (SELECT 1 FROM anotacao_sessao n JOIN atendimento a ON a.id = n.atendimento_id
                            WHERE a.cliente_id = e.cliente_id));
        qtd_saude := cardinality(v_ids);
        IF qtd_saude > 0 THEN
            PERFORM set_config('app.expurgo_autorizado', 'on', true);
            SELECT coalesce(array_agg(id), '{}') INTO v_pront FROM prontuario WHERE cliente_id = ANY (v_ids);
            DELETE FROM prontuario_versao WHERE prontuario_id = ANY (v_pront);
            DELETE FROM anotacao_sessao n USING atendimento a
             WHERE a.id = n.atendimento_id AND a.cliente_id = ANY (v_ids);
            DELETE FROM resposta_formulario WHERE cliente_id = ANY (v_ids);   -- a cascata apaga o link_acesso
            DELETE FROM prontuario WHERE id = ANY (v_pront);
            DELETE FROM historico_alteracao
             WHERE tabela = 'prontuario' AND registro_pk = ANY (v_pront) AND criado_em < v_limite;
            UPDATE aceite_termo t SET ip_origem = NULL, user_agent = ''
              FROM versao_termo vt
             WHERE vt.id = t.versao_termo_id AND vt.tipo IN ('SAUDE', 'PROCEDIMENTO')
               AND t.cliente_id = ANY (v_ids) AND (t.ip_origem IS NOT NULL OR t.user_agent <> '');
            PERFORM set_config('app.expurgo_autorizado', 'off', true);
            INSERT INTO log_auditoria (acao, tabela, detalhes)
            VALUES ('Expurgo do registro de saude por prazo (LGPD art. 16)', 'prontuario',
                    jsonb_build_object('qtd_clientes', qtd_saude, 'prazo_dias', p_retencao_saude_dias));
        END IF;
    EXCEPTION WHEN OTHERS THEN
        etapas_com_falha := etapas_com_falha || 'saude'::text;
        RAISE WARNING 'sp_purgar_dados_expirados/saude: %', SQLERRM;
    END;
END $$
"""

# = sql/v0074_anonimizar_cliente + indicacao, aceites, lista de espera anonima e detalhes LGPD
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
    v_tem_saude   boolean;
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
    SELECT coalesce(array_agg(id), '{}') INTO v_espera_ids FROM lista_espera
     WHERE cliente_id = p_cliente_id OR (v_cli.telefone IS NOT NULL AND telefone_contato = v_cli.telefone);
    SELECT coalesce(array_agg(id), '{}') INTO v_pront_ids  FROM prontuario    WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(id), '{}') INTO v_cart_ids   FROM carteira      WHERE cliente_id = p_cliente_id;
    SELECT coalesce(array_agg(n.id), '{}') INTO v_nps_ids
      FROM avaliacao_nps n WHERE n.atendimento_id = ANY (v_atend_ids);
    SELECT coalesce(array_agg(n.id), '{}') INTO v_notif_ids
      FROM notificacao n WHERE n.atendimento_id = ANY (v_atend_ids);

    -- 1. cadastro (tg_cliente_atualizado_em preenche atualizado_em); o codigo de indicacao e quem
    --    indicou saem: com eles da para reidentificar (art. 13 §4)
    UPDATE cliente
       SET nome = v_pseudo, cpf = NULL, email = NULL, telefone = NULL, cep = NULL,
           endereco = NULL, data_nascimento = NULL, codigo_indicacao = NULL, cliente_indicador_id = NULL,
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
            OR notificacao_id = ANY (v_notif_ids)
            OR resposta_formulario_id IN (SELECT id FROM resposta_formulario WHERE cliente_id = p_cliente_id));

    -- 4. OTP (telefone, pseudo-e-mail do fluxo so-telefone, e-mail)
    DELETE FROM codigo_otp
     WHERE (v_cli.telefone IS NOT NULL
            AND (telefone = v_cli.telefone OR identificador = 'sms+' || v_cli.telefone || '@shivazen.local'))
        OR (v_cli.email IS NOT NULL AND upper(identificador) = upper(v_cli.email));

    -- 5. lista de espera (do cadastro e a inscricao anonima com o mesmo celular) e texto das notificacoes
    DELETE FROM lista_espera WHERE id = ANY (v_espera_ids);
    UPDATE notificacao SET mensagem = '' WHERE id = ANY (v_notif_ids) AND mensagem <> '';

    -- 6. dado de saude sem atendimento REALIZADO: fim da finalidade (a cascata apaga o link_acesso)
    DELETE FROM resposta_formulario r
     WHERE r.cliente_id = p_cliente_id
       AND NOT EXISTS (SELECT 1 FROM atendimento a WHERE a.id = r.atendimento_id AND a.status = 'REALIZADO');

    -- 6b. aceites: o do termo LGPD perde IP/user-agent; SAUDE e PROCEDIMENTO so quando nao ha mais
    --     registro de saude (enquanto houver, a prova do consentimento fica com o registro)
    v_tem_saude := EXISTS (SELECT 1 FROM prontuario p WHERE p.cliente_id = p_cliente_id)
                OR EXISTS (SELECT 1 FROM resposta_formulario r WHERE r.cliente_id = p_cliente_id)
                OR EXISTS (SELECT 1 FROM anotacao_sessao n WHERE n.atendimento_id = ANY (v_atend_ids));
    UPDATE aceite_termo t SET ip_origem = NULL, user_agent = ''
      FROM versao_termo vt
     WHERE vt.id = t.versao_termo_id AND t.cliente_id = p_cliente_id
       AND (t.ip_origem IS NOT NULL OR t.user_agent <> '')
       AND (vt.tipo = 'LGPD' OR NOT v_tem_saude);

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
    -- detalhes da titular (pedidos e revogacoes LGPD, migracao 0034) saem; a acao e a data ficam
    UPDATE log_auditoria SET detalhes = NULL
     WHERE tabela = 'cliente' AND registro_pk = p_cliente_id AND detalhes IS NOT NULL
       AND (acao LIKE 'migration 0034%' OR acao LIKE 'LGPD: %');

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

# ─── triggers de imutabilidade: expurgo so com a flag E 20 anos; aceite so minimiza ────────────
FN_PRONTUARIO_VERSAO_IMUTAVEL = """
CREATE OR REPLACE FUNCTION fn_prontuario_versao_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        -- so o expurgo de saude (sp_purgar_dados_expirados liga a flag local da transacao) e so do
        -- que ja tem 20 anos: a flag sozinha nao apaga historico clinico recente
        IF coalesce(current_setting('app.expurgo_autorizado', true), '') = 'on'
           AND OLD.criado_em < now() - interval '20 years' THEN
            RETURN OLD;
        END IF;
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
END $$
"""

FN_ANOTACAO_SESSAO_IMUTAVEL = """
CREATE OR REPLACE FUNCTION fn_anotacao_sessao_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND coalesce(current_setting('app.expurgo_autorizado', true), '') = 'on'
       AND OLD.criado_em < now() - interval '20 years' THEN
        RETURN OLD;                                  -- expurgo de saude aos 20 anos
    END IF;
    RAISE EXCEPTION 'anotacao_sessao e append-only: % bloqueado', TG_OP
        USING DETAIL = 'anotacao_sessao.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
END $$
"""

TG_ANOTACAO_SESSAO = [
    'DROP TRIGGER IF EXISTS tg_anotacao_sessao_imutavel ON anotacao_sessao',
    'CREATE TRIGGER tg_anotacao_sessao_imutavel BEFORE UPDATE OR DELETE ON anotacao_sessao '
    'FOR EACH ROW EXECUTE FUNCTION fn_anotacao_sessao_imutavel()',
]

FN_ACEITE_TERMO_IMUTAVEL = """
CREATE OR REPLACE FUNCTION fn_aceite_termo_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'aceite_termo e prova legal (LGPD art. 8): exclusao bloqueada'
            USING DETAIL = 'aceite_termo.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- mutacoes aceitas: SET NULL do atendimento e a minimizacao LGPD (pseudonimizacao/expurgo apagam
    -- IP e user-agent); cliente, versao, hash e data do aceite nunca mudam
    IF (NEW.id, NEW.cliente_id, NEW.versao_termo_id, NEW.conteudo_hash, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.cliente_id, OLD.versao_termo_id, OLD.conteudo_hash, OLD.criado_em)
       OR (NEW.ip_origem IS DISTINCT FROM OLD.ip_origem AND NEW.ip_origem IS NOT NULL)
       OR (NEW.user_agent IS DISTINCT FROM OLD.user_agent AND NEW.user_agent <> '')
       OR (NEW.atendimento_id IS DISTINCT FROM OLD.atendimento_id AND NEW.atendimento_id IS NOT NULL) THEN
        RAISE EXCEPTION 'aceite_termo e prova legal (LGPD art. 8): registro imutavel'
            USING DETAIL = 'aceite_termo.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$
"""

# = sql/v0056_triggers.FN_REGISTRAR_HISTORICO + o expurgo do prontuario nao vira copia na trilha
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
    -- expurgo de saude aos 20 anos (sp_purgar_dados_expirados): o prontuario expurgado nao deixa a
    -- propria copia em dados_antes (a procedure registra a quantidade em log_auditoria)
    IF TG_OP = 'DELETE' AND TG_TABLE_NAME = 'prontuario'
       AND coalesce(current_setting('app.expurgo_autorizado', true), '') = 'on' THEN
        IF (to_jsonb(OLD) ->> 'atualizado_em')::timestamptz < now() - interval '20 years' THEN
            RETURN NULL;
        END IF;
    END IF;

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

# assinaturas antigas (0057/0058) saem: o CREATE OR REPLACE com parametro novo criaria uma sobrecarga
DROPAR_ASSINATURAS_ANTIGAS = [
    'DROP PROCEDURE IF EXISTS sp_aplicar_retencao_lgpd(integer, integer, integer, integer, boolean, integer, '
    'integer, integer)',
    'DROP PROCEDURE IF EXISTS sp_purgar_dados_expirados(integer, integer, integer, integer, integer, integer, '
    'integer, integer, integer, text[])',
    'DROP FUNCTION IF EXISTS fn_clientes_para_anonimizar(integer, integer, integer, timestamptz)',
]
DROPAR_ASSINATURAS_NOVAS = [
    'DROP PROCEDURE IF EXISTS sp_aplicar_retencao_lgpd(integer, integer, integer, integer, integer, boolean, '
    'integer, integer, integer)',
    'DROP PROCEDURE IF EXISTS sp_purgar_dados_expirados(integer, integer, integer, integer, integer, integer, '
    'integer, integer, integer, integer, integer, text[])',
    'DROP FUNCTION IF EXISTS fn_clientes_para_anonimizar(integer, integer, integer, integer, timestamptz)',
]

CRIAR = [
    *DROPAR_ASSINATURAS_ANTIGAS,
    FN_CLIENTES_PARA_ANONIMIZAR,
    SP_ANONIMIZAR_CLIENTE,
    SP_APLICAR_RETENCAO_LGPD,
    SP_PURGAR_DADOS_EXPIRADOS,
    FN_PRONTUARIO_VERSAO_IMUTAVEL,
    FN_ANOTACAO_SESSAO_IMUTAVEL,
    *TG_ANOTACAO_SESSAO,
    FN_ACEITE_TERMO_IMUTAVEL,
    FN_REGISTRAR_HISTORICO,
]
