"""0069 — link_acesso sem o alvo atendimento (o link legado GERENCIAR_AGENDAMENTO era o unico).

CONGELADO: importado so pela 0069_papeis_e_links (e pelo reverso da 0071). Mudanca = modulo novo +
migration nova com CREATE OR REPLACE.

- fn_link_acesso_imutavel: a versao da 0049 sem atendimento_id (a coluna sai na 0069);
- sp_anonimizar_cliente: a versao da 0058 sem ``OR atendimento_id = ANY (v_atend_ids)`` no passo 3
  (revogar os links do titular). O resto e identico.
"""

FN_LINK_ACESSO_IMUTAVEL = """
CREATE OR REPLACE FUNCTION fn_link_acesso_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- DELETE livre (purga/cascata). UPDATE: so marcar usado_em e revogado_em, uma vez cada.
    IF (NEW.id, NEW.finalidade, NEW.notificacao_id, NEW.resposta_formulario_id,
        NEW.cliente_id, NEW.profissional_id, NEW.token_hash, NEW.expira_em, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.finalidade, OLD.notificacao_id, OLD.resposta_formulario_id,
        OLD.cliente_id, OLD.profissional_id, OLD.token_hash, OLD.expira_em, OLD.criado_em)
       OR (OLD.usado_em IS NOT NULL AND NEW.usado_em IS DISTINCT FROM OLD.usado_em)
       OR (OLD.revogado_em IS NOT NULL AND NEW.revogado_em IS DISTINCT FROM OLD.revogado_em) THEN
        RAISE EXCEPTION 'link_acesso: so usado_em/revogado_em podem ser preenchidos (uma vez)'
            USING DETAIL = 'link_acesso.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""

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

CRIAR = [FN_LINK_ACESSO_IMUTAVEL, SP_ANONIMIZAR_CLIENTE]
