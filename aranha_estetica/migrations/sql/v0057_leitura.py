"""0057 (L2) — extensoes, utilitarias, busca trigram, funcoes de leitura e views (spec §5.1, §5.4, §5.5).

CONGELADO: importado so pela 0057_logica_views_funcoes. Mudanca = modulo novo + migration nova
com CREATE OR REPLACE.

Funcoes de LEITURA sao espelho do Python (o app continua usando o Python: SlotService,
CompraPacote.saldo_por_procedimento, utils/nps, ComissaoService, services/consentimentos,
LgpdService.candidatos_purga) — travadas pelo tests/test_pg_paridade.py. Views sao para
relatorio/BI/SQL ad hoc: o app nao le views.

SQL puro (sem laco) = corpo BEGIN ATOMIC/RETURN (PG >= 14): analisado na criacao, guarda as
dependencias — renomear/dropar coluna usada falha em vez de quebrar em producao. PL/pgSQL
(recorrencia, slots) nao tem essa protecao: a paridade e a rede.
"""

EXTENSOES = [
    'CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public',   # ix_cliente_nome_trgm
    'CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public',  # fn_sem_acento
]

# ─── §5.1 utilitarias ────────────────────────────────────────────────────────
FN_FUSO_CLINICA = """
CREATE OR REPLACE FUNCTION fn_fuso_clinica() RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN 'America/Sao_Paulo'
"""

FN_DATA_LOCAL = """
CREATE OR REPLACE FUNCTION fn_data_local(p_instante timestamptz) RETURNS date
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
RETURN (p_instante AT TIME ZONE fn_fuso_clinica())::date
"""

# unaccent() e STABLE; o wrapper com dicionario explicito pode ser IMMUTABLE (padrao da doc do
# pg_trgm) — e o que permite o indice ix_cliente_nome_trgm.
FN_SEM_ACENTO = """
CREATE OR REPLACE FUNCTION fn_sem_acento(p_texto text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
RETURN lower(public.unaccent('public.unaccent'::regdictionary, p_texto))
"""

IX_CLIENTE_NOME_TRGM = (
    'CREATE INDEX IF NOT EXISTS ix_cliente_nome_trgm ON cliente USING gin (fn_sem_acento(nome) gin_trgm_ops)'
)

# ─── §5.5 funcoes de leitura ─────────────────────────────────────────────────
# Subconjunto de RRULE (utils/recorrencia.REGEX_SUBCONJUNTO): casa um dia com a regra (DTSTART = p_inicio)
FN_RECORRENCIA_CASA_DIA = """
CREATE OR REPLACE FUNCTION fn_recorrencia_casa_dia(
    p_freq text, p_intervalo integer, p_byday text[], p_bymonthday integer,
    p_inicio date, p_dia date)
RETURNS boolean
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
    v_codigo text := (ARRAY['MO','TU','WE','TH','FR','SA','SU'])[extract(isodow FROM p_dia)::int];
    v_item   text;
    v_ord    text;
BEGIN
    IF p_dia < p_inicio THEN
        RETURN false;
    END IF;
    IF p_freq = 'DAILY' THEN
        RETURN (p_dia - p_inicio) % p_intervalo = 0
           AND (p_byday IS NULL OR v_codigo = ANY (p_byday));
    ELSIF p_freq = 'WEEKLY' THEN            -- semanas contadas a partir da segunda (WKST=MO)
        IF ((date_trunc('week', p_dia::timestamp)::date
             - date_trunc('week', p_inicio::timestamp)::date) / 7) % p_intervalo <> 0 THEN
            RETURN false;
        END IF;
        IF p_byday IS NULL THEN
            RETURN extract(isodow FROM p_dia) = extract(isodow FROM p_inicio);
        END IF;
        RETURN v_codigo = ANY (p_byday);
    ELSIF p_freq = 'MONTHLY' THEN
        IF ((extract(year FROM p_dia) - extract(year FROM p_inicio)) * 12
            + extract(month FROM p_dia) - extract(month FROM p_inicio))::int % p_intervalo <> 0 THEN
            RETURN false;
        END IF;
        IF p_bymonthday IS NOT NULL THEN
            RETURN extract(day FROM p_dia) = p_bymonthday;
        END IF;
        IF p_byday IS NULL THEN                 -- mesmo dia do mes do DTSTART (31 pula meses curtos)
            RETURN extract(day FROM p_dia) = extract(day FROM p_inicio);
        END IF;
        FOREACH v_item IN ARRAY p_byday LOOP
            CONTINUE WHEN right(v_item, 2) <> v_codigo;
            v_ord := left(v_item, length(v_item) - 2);
            IF v_ord = '' THEN
                RETURN true;
            ELSIF v_ord = '-1' THEN
                IF extract(month FROM p_dia + 7) <> extract(month FROM p_dia) THEN RETURN true; END IF;
            ELSIF (extract(day FROM p_dia)::int - 1) / 7 + 1 = v_ord::int THEN
                RETURN true;
            END IF;
        END LOOP;
        RETURN false;
    END IF;
    RAISE EXCEPTION 'FREQ fora do subconjunto suportado: %', p_freq;
END $$
"""

# Ocorrencias de um bloqueio recorrente que tocam [p_inicio, p_fim) — replica
# AgendaExcecao.ocorrencias (dateutil): rrule.between(inicio - duracao, fim_busca, inc=True),
# descarta inicio >= fim_busca, mantem as que intersectam; DTSTART e horarios na hora LOCAL;
# DTSTART que nao casa com a regra NAO e ocorrencia (comportamento do dateutil).
FN_OCORRENCIAS_RECORRENCIA = """
CREATE OR REPLACE FUNCTION fn_ocorrencias_recorrencia(
    p_regra text, p_dtstart timestamptz, p_duracao interval, p_data_fim_recorrencia date,
    p_inicio timestamptz, p_fim timestamptz)
RETURNS SETOF tstzrange
LANGUAGE plpgsql STABLE SET search_path = public, pg_catalog AS $$
DECLARE
    v_tz         constant text := fn_fuso_clinica();
    v_d0         timestamp := p_dtstart AT TIME ZONE v_tz;
    v_freq       text;
    v_intervalo  integer := 1;
    v_byday      text[];
    v_bymonthday integer;
    v_parte      text;
    v_valor      text;
    v_limite     timestamptz := p_fim;
    v_dia        date;
    v_ini        timestamptz;
BEGIN
    FOREACH v_parte IN ARRAY string_to_array(p_regra, ';') LOOP
        v_valor := split_part(v_parte, '=', 2);
        CASE split_part(v_parte, '=', 1)
            WHEN 'FREQ'       THEN v_freq := v_valor;
            WHEN 'INTERVAL'   THEN v_intervalo := v_valor::int;
            WHEN 'BYDAY'      THEN v_byday := string_to_array(v_valor, ',');
            WHEN 'BYMONTHDAY' THEN v_bymonthday := v_valor::int;
            ELSE RAISE EXCEPTION 'regra fora do subconjunto suportado: %', p_regra;
        END CASE;
    END LOOP;
    IF p_data_fim_recorrencia IS NOT NULL THEN     -- inclusivo: ate o fim do dia local
        v_limite := least(p_fim, (p_data_fim_recorrencia + 1)::timestamp AT TIME ZONE v_tz);
    END IF;
    FOR v_dia IN
        SELECT d::date FROM generate_series(
            greatest(v_d0::date, ((p_inicio - p_duracao) AT TIME ZONE v_tz)::date)::timestamp,
            (v_limite AT TIME ZONE v_tz)::date::timestamp,
            interval '1 day') AS d
    LOOP
        CONTINUE WHEN NOT fn_recorrencia_casa_dia(v_freq, v_intervalo, v_byday, v_bymonthday, v_d0::date, v_dia);
        v_ini := (v_dia + v_d0::time) AT TIME ZONE v_tz;
        IF v_ini >= p_inicio - p_duracao AND v_ini < v_limite
           AND v_ini + p_duracao > p_inicio AND v_ini < p_fim THEN
            RETURN NEXT tstzrange(v_ini, v_ini + p_duracao, '[)');
        END IF;
    END LOOP;
END $$
"""

# Espelho de SlotService.slots_livres (services/disponibilidade.py, agenda unificada 0048).
# p_agora permite teste deterministico (o Python usa timezone.now()). NAO e fonte do agendamento
# (spec §7.4): o app continua no SlotService; isto e espelho p/ relatorio/integracao.
FN_HORARIOS_LIVRES = """
CREATE OR REPLACE FUNCTION fn_horarios_livres(
    p_profissional_id         bigint,
    p_data                    date,
    p_procedimento_id         bigint      DEFAULT NULL,
    p_atendimento_ignorado_id bigint      DEFAULT NULL,
    p_agora                   timestamptz DEFAULT now())
RETURNS SETOF time
LANGUAGE plpgsql STABLE SET search_path = public, pg_catalog AS $$
DECLARE
    v_tz          constant text := fn_fuso_clinica();
    v_passo       constant interval := interval '30 minutes';
    v_agora_local timestamp := p_agora AT TIME ZONE v_tz;
    v_dia_inicio  timestamptz := p_data::timestamp AT TIME ZONE v_tz;
    v_dia_fim     timestamptz := (p_data + 1)::timestamp AT TIME ZONE v_tz;
    v_antec_min   integer;
    v_antec_max   integer;
    v_duracao     interval := interval '30 minutes';
    v_intervalo   interval := interval '0';
    v_limite_min  timestamptz;
    v_hd_inicio   time;
    v_hd_fim      time;
    v_oc_ini      timestamptz[] := '{}';
    v_oc_fim      timestamptz[] := '{}';
    v_bl_ini      timestamptz[] := '{}';
    v_bl_fim      timestamptz[] := '{}';
    v_livres      time[] := '{}';
    v_janela      record;
    v_bl          record;
    v_ocorr       tstzrange;
    v_slot        timestamptz;
    v_fim_slot    timestamptz;
    v_fim_exped   timestamptz;
BEGIN
    SELECT antecedencia_minima_horas, antecedencia_maxima_dias INTO v_antec_min, v_antec_max
      FROM profissional WHERE id = p_profissional_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    IF p_data < v_agora_local::date
       OR p_data > (v_agora_local + make_interval(days => v_antec_max))::date THEN
        RETURN;                                           -- passado ou alem da antecedencia maxima
    END IF;
    v_limite_min := p_agora + make_interval(hours => v_antec_min);

    -- excecoes ativas do profissional OU da clinica inteira (profissional NULL)
    IF EXISTS (SELECT 1 FROM agenda_excecao e             -- folga do profissional ou feriado/recesso
                WHERE e.eh_ativo AND e.tipo = 'FOLGA'
                  AND (e.profissional_id = p_profissional_id OR e.profissional_id IS NULL)
                  AND e.data_inicio <= p_data AND e.data_fim >= p_data) THEN
        RETURN;
    END IF;
    SELECT e.hora_inicio, e.hora_fim INTO v_hd_inicio, v_hd_fim  -- horario diferente substitui a grade
      FROM agenda_excecao e
     WHERE e.eh_ativo AND e.tipo = 'HORARIO_DIFERENTE'
       AND (e.profissional_id = p_profissional_id OR e.profissional_id IS NULL)
       AND e.data_inicio <= p_data AND e.data_fim >= p_data
     ORDER BY e.id
     LIMIT 1;

    IF p_procedimento_id IS NOT NULL THEN
        SELECT CASE WHEN coalesce(pr.duracao_minutos, 0) <> 0
                    THEN make_interval(mins => pr.duracao_minutos) ELSE v_passo END,
               make_interval(mins => coalesce(pr.intervalo_minutos, 0))
          INTO v_duracao, v_intervalo
          FROM procedimento pr WHERE pr.id = p_procedimento_id;
        IF NOT FOUND THEN
            v_duracao := v_passo;
            v_intervalo := interval '0';
        END IF;
    END IF;

    -- atendimentos que ocupam a agenda e intersectam o dia (+ intervalo do procedimento deles)
    SELECT coalesce(array_agg(a.data_hora_inicio ORDER BY a.id), '{}'),
           coalesce(array_agg(a.data_hora_fim + make_interval(mins => coalesce(pr.intervalo_minutos, 0))
                              ORDER BY a.id), '{}')
      INTO v_oc_ini, v_oc_fim
      FROM atendimento a
      JOIN procedimento pr ON pr.id = a.procedimento_id
     WHERE a.profissional_id = p_profissional_id
       AND a.data_hora_inicio < v_dia_fim AND a.data_hora_fim > v_dia_inicio
       AND a.status IN ('PENDENTE', 'AGENDADO', 'CONFIRMADO')
       AND (p_atendimento_ignorado_id IS NULL OR a.id <> p_atendimento_ignorado_id);

    -- bloqueios pontuais que intersectam o dia + ocorrencias dos recorrentes
    FOR v_bl IN
        SELECT e.data_hora_inicio, e.data_hora_fim, e.regra_recorrencia, e.data_fim_recorrencia
          FROM agenda_excecao e
         WHERE e.eh_ativo AND e.tipo = 'BLOQUEIO'
           AND (e.profissional_id = p_profissional_id OR e.profissional_id IS NULL)
           AND e.data_hora_inicio < v_dia_fim
           AND (e.regra_recorrencia IS NOT NULL OR e.data_hora_fim > v_dia_inicio)
    LOOP
        IF v_bl.regra_recorrencia IS NULL THEN
            v_bl_ini := v_bl_ini || v_bl.data_hora_inicio;
            v_bl_fim := v_bl_fim || v_bl.data_hora_fim;
        ELSE
            FOR v_ocorr IN
                SELECT r FROM fn_ocorrencias_recorrencia(v_bl.regra_recorrencia, v_bl.data_hora_inicio,
                                                         v_bl.data_hora_fim - v_bl.data_hora_inicio,
                                                         v_bl.data_fim_recorrencia, v_dia_inicio, v_dia_fim) AS r
            LOOP
                v_bl_ini := v_bl_ini || lower(v_ocorr);
                v_bl_fim := v_bl_fim || upper(v_ocorr);
            END LOOP;
        END IF;
    END LOOP;

    FOR v_janela IN
        SELECT v_hd_inicio AS hora_inicio, v_hd_fim AS hora_fim WHERE v_hd_inicio IS NOT NULL
        UNION ALL
        SELECT h.hora_inicio, h.hora_fim FROM agenda_horario h
         WHERE v_hd_inicio IS NULL
           AND h.profissional_id = p_profissional_id
           AND h.dia_semana = extract(isodow FROM p_data)::int
    LOOP
        v_slot      := (p_data + v_janela.hora_inicio) AT TIME ZONE v_tz;
        v_fim_exped := (p_data + v_janela.hora_fim) AT TIME ZONE v_tz;
        WHILE v_slot + v_duracao <= v_fim_exped LOOP     -- o procedimento inteiro cabe no expediente
            IF v_slot >= v_limite_min THEN
                v_fim_slot := v_slot + v_duracao;
                IF NOT EXISTS (SELECT 1 FROM unnest(v_oc_ini, v_oc_fim) AS o(ini, fim)
                                WHERE v_slot < o.fim AND v_fim_slot + v_intervalo > o.ini)
                   AND NOT EXISTS (SELECT 1 FROM unnest(v_bl_ini, v_bl_fim) AS b(ini, fim)
                                    WHERE v_slot < b.fim AND v_fim_slot > b.ini) THEN
                    v_livres := v_livres || (v_slot AT TIME ZONE v_tz)::time;
                END IF;
            END IF;
            v_slot := v_slot + v_passo;
        END LOOP;
    END LOOP;

    RETURN QUERY SELECT DISTINCT t FROM unnest(v_livres) AS t ORDER BY t;
END $$
"""

# Espelho de CompraPacote.saldo_por_procedimento (itens CONGELADOS na venda, 0052)
FN_SALDO_PACOTE = """
CREATE OR REPLACE FUNCTION fn_saldo_pacote(p_compra_pacote_id bigint)
RETURNS TABLE (compra_pacote_item_id bigint, procedimento_id bigint, procedimento_nome varchar,
               qtd_total smallint, qtd_usada bigint, qtd_restante bigint)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT i.id, i.procedimento_id, i.procedimento_nome, i.qtd_sessoes,
           count(cs.id), greatest(i.qtd_sessoes - count(cs.id), 0)
      FROM compra_pacote_item i
      LEFT JOIN consumo_sessao cs ON cs.compra_pacote_item_id = i.id
     WHERE i.compra_pacote_id = p_compra_pacote_id
     GROUP BY i.id
     ORDER BY i.id;
END
"""

# Espelho de utils/nps.resumo_nps (arredondamento "meio p/ cima", decisao D7 do spec §7.1)
FN_NPS_PERIODO = """
CREATE OR REPLACE FUNCTION fn_nps_periodo(p_inicio timestamptz DEFAULT NULL, p_fim timestamptz DEFAULT NULL)
RETURNS TABLE (qtd_respostas bigint, qtd_promotores bigint, qtd_neutros bigint, qtd_detratores bigint,
               nps integer, nota_media numeric)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT count(*),
           count(*) FILTER (WHERE n.nota >= 9),
           count(*) FILTER (WHERE n.nota BETWEEN 7 AND 8),
           count(*) FILTER (WHERE n.nota <= 6),
           CASE WHEN count(*) > 0 THEN
                round((count(*) FILTER (WHERE n.nota >= 9) - count(*) FILTER (WHERE n.nota <= 6)) * 100.0
                      / count(*))::integer END,
           round(avg(n.nota), 1)
      FROM avaliacao_nps n
     WHERE (p_inicio IS NULL OR n.criado_em >= p_inicio)
       AND (p_fim IS NULL OR n.criado_em < p_fim);
END
"""

# Espelho de ComissaoService.saldo_a_pagar (livro-razao 0053: CREDITO - PAGAMENTO - ESTORNO)
FN_SALDO_COMISSAO = """
CREATE OR REPLACE FUNCTION fn_saldo_comissao(p_profissional_id bigint, p_ate timestamptz DEFAULT NULL)
RETURNS numeric
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT coalesce(sum(CASE WHEN m.tipo = 'CREDITO' THEN m.valor ELSE -m.valor END), 0)::numeric(12,2)
      FROM movimento_comissao m
     WHERE m.profissional_id = p_profissional_id
       AND (p_ate IS NULL OR m.criado_em <= p_ate);
END
"""

# Espelho de services/consentimentos.vigente (ultimo registro do par manda; sem linha = false)
FN_TEM_CONSENTIMENTO = """
CREATE OR REPLACE FUNCTION fn_tem_consentimento(p_cliente_id bigint, p_finalidade text, p_canal text)
RETURNS boolean
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT coalesce((SELECT c.eh_concedido FROM consentimento c
                      WHERE c.cliente_id = p_cliente_id AND c.finalidade = p_finalidade AND c.canal = p_canal
                      ORDER BY c.registrado_em DESC, c.id DESC LIMIT 1), false);
END
"""

# Espelho de LgpdService.candidatos_purga (usado por sp_aplicar_retencao_lgpd, 0058)
FN_CLIENTES_PARA_ANONIMIZAR = """
CREATE OR REPLACE FUNCTION fn_clientes_para_anonimizar(
    p_retencao_cliente_dias integer, p_carencia_soft_dias integer, p_retencao_saude_dias integer,
    p_agora timestamptz DEFAULT now())
RETURNS SETOF bigint
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT c.id FROM cliente c
     WHERE ((c.deletado_em IS NULL AND c.criado_em < p_agora - make_interval(days => p_retencao_cliente_dias))
            OR c.deletado_em < p_agora - make_interval(days => p_carencia_soft_dias))
       AND c.nome NOT LIKE '[ANONIMIZADO-%'
       AND NOT EXISTS (SELECT 1 FROM atendimento a WHERE a.cliente_id = c.id
                          AND a.data_hora_inicio >= p_agora - make_interval(days => p_retencao_cliente_dias))
       AND NOT EXISTS (SELECT 1 FROM atendimento a WHERE a.cliente_id = c.id AND a.status = 'REALIZADO'
                          AND a.data_hora_inicio >= p_agora - make_interval(days => p_retencao_saude_dias))
       AND NOT EXISTS (SELECT 1 FROM prontuario p WHERE p.cliente_id = c.id
                          AND (p.alergias <> '' OR p.contraindicacoes <> '' OR p.historico_saude <> ''
                               OR p.medicamentos_uso <> '' OR p.observacoes_gerais <> '' OR p.respostas <> '{}'::jsonb))
       AND NOT EXISTS (SELECT 1 FROM prontuario_versao v JOIN prontuario p ON p.id = v.prontuario_id
                        WHERE p.cliente_id = c.id)
       AND NOT EXISTS (SELECT 1 FROM compra_pacote cp WHERE cp.cliente_id = c.id)
     ORDER BY c.id;
END
"""

# ─── §5.4 views de relatorio ─────────────────────────────────────────────────
# Agenda por dia local (filtre por data_hora_inicio em intervalo p/ usar indice; data_local e derivada)
VW_AGENDA_DIA = """
CREATE OR REPLACE VIEW vw_agenda_dia AS
SELECT a.id                                              AS atendimento_id,
       fn_data_local(a.data_hora_inicio)                 AS data_local,
       (a.data_hora_inicio AT TIME ZONE fn_fuso_clinica())::time AS hora_inicio_local,
       (a.data_hora_fim    AT TIME ZONE fn_fuso_clinica())::time AS hora_fim_local,
       a.profissional_id, pf.nome AS profissional_nome,
       a.cliente_id, c.nome AS cliente_nome, c.telefone AS cliente_telefone,
       a.procedimento_id, pr.nome AS procedimento_nome,
       a.status, a.eh_retorno, a.valor_cobrado,
       (cs.id IS NOT NULL)                               AS eh_sessao_pacote,
       a.data_hora_inicio, a.data_hora_fim
  FROM atendimento a
  JOIN profissional pf ON pf.id = a.profissional_id
  JOIN cliente c       ON c.id  = a.cliente_id
  JOIN procedimento pr ON pr.id = a.procedimento_id
  LEFT JOIN consumo_sessao cs ON cs.atendimento_id = a.id
"""

# Faturamento por mes local — mesma regra de services/financeiro (D20): avulso = REALIZADO, nao
# retorno, valor > 0, sem consumo de pacote; pacote = liquido do reembolso, no mes da venda
VW_FATURAMENTO_MENSAL = """
CREATE OR REPLACE VIEW vw_faturamento_mensal AS
WITH avulsos AS (
    SELECT date_trunc('month', a.data_hora_inicio AT TIME ZONE fn_fuso_clinica())::date AS mes,
           sum(a.valor_cobrado) AS valor, count(*) AS qtd
      FROM atendimento a
      LEFT JOIN consumo_sessao cs ON cs.atendimento_id = a.id
     WHERE a.status = 'REALIZADO' AND NOT a.eh_retorno AND a.valor_cobrado > 0 AND cs.id IS NULL
     GROUP BY 1
), pacotes AS (
    SELECT date_trunc('month', cp.criado_em AT TIME ZONE fn_fuso_clinica())::date AS mes,
           sum(cp.valor_pago - cp.valor_reembolsado) AS valor, count(*) AS qtd
      FROM compra_pacote cp
     WHERE cp.valor_pago > cp.valor_reembolsado
     GROUP BY 1
)
SELECT coalesce(a.mes, p.mes)                                        AS mes,
       coalesce(a.valor, 0)::numeric(12,2)                           AS valor_atendimentos,
       coalesce(a.qtd, 0)                                            AS qtd_atendimentos,
       coalesce(p.valor, 0)::numeric(12,2)                           AS valor_pacotes,
       coalesce(p.qtd, 0)                                            AS qtd_pacotes,
       (coalesce(a.valor, 0) + coalesce(p.valor, 0))::numeric(12,2)  AS valor_total,
       CASE WHEN a.qtd > 0 THEN round(a.valor / a.qtd, 2) END        AS valor_ticket_medio
  FROM avulsos a
  FULL JOIN pacotes p ON p.mes = a.mes
"""

VW_NPS_MENSAL = """
CREATE OR REPLACE VIEW vw_nps_mensal AS
SELECT date_trunc('month', n.criado_em AT TIME ZONE fn_fuso_clinica())::date AS mes,
       count(*)                                       AS qtd_respostas,
       count(*) FILTER (WHERE n.nota >= 9)            AS qtd_promotores,
       count(*) FILTER (WHERE n.nota BETWEEN 7 AND 8) AS qtd_neutros,
       count(*) FILTER (WHERE n.nota <= 6)            AS qtd_detratores,
       round((count(*) FILTER (WHERE n.nota >= 9) - count(*) FILTER (WHERE n.nota <= 6)) * 100.0
             / count(*))::integer                     AS nps,
       round(avg(n.nota), 1)                          AS nota_media
  FROM avaliacao_nps n
 GROUP BY 1
"""

# 1 linha por CREDITO com status derivado (os UNIQUE parciais garantem <= 1 pagamento e <= 1 estorno)
# = MovimentoComissao.objects.creditos_com_status()
VW_COMISSAO = """
CREATE OR REPLACE VIEW vw_comissao AS
SELECT c.id AS movimento_comissao_id, c.profissional_id, c.atendimento_id, c.regra_comissao_id,
       c.valor, c.criado_em,
       p.criado_em AS pago_em,
       e.criado_em AS estornado_em,
       CASE WHEN e.id IS NOT NULL THEN 'ESTORNADA'
            WHEN p.id IS NOT NULL THEN 'PAGA'
            ELSE 'PENDENTE' END AS status
  FROM movimento_comissao c
  LEFT JOIN movimento_comissao p ON p.movimento_comissao_origem_id = c.id AND p.tipo = 'PAGAMENTO'
  LEFT JOIN movimento_comissao e ON e.movimento_comissao_origem_id = c.id AND e.tipo = 'ESTORNO'
 WHERE c.tipo = 'CREDITO'
"""

VW_COMISSAO_SALDO = """
CREATE OR REPLACE VIEW vw_comissao_saldo AS
SELECT pf.id AS profissional_id, pf.nome AS profissional_nome,
       coalesce(sum(m.valor) FILTER (WHERE m.tipo = 'CREDITO'), 0)::numeric(12,2)   AS valor_creditado,
       coalesce(sum(m.valor) FILTER (WHERE m.tipo = 'PAGAMENTO'), 0)::numeric(12,2) AS valor_pago,
       coalesce(sum(m.valor) FILTER (WHERE m.tipo = 'ESTORNO'), 0)::numeric(12,2)   AS valor_estornado,
       coalesce(sum(CASE WHEN m.tipo = 'CREDITO' THEN m.valor ELSE -m.valor END), 0)::numeric(12,2)
                                                                                   AS valor_saldo_a_pagar,
       (SELECT coalesce(sum(v.valor), 0) FROM vw_comissao v
         WHERE v.profissional_id = pf.id AND v.status = 'PENDENTE')::numeric(12,2) AS valor_pendente
  FROM profissional pf
  LEFT JOIN movimento_comissao m ON m.profissional_id = pf.id
 GROUP BY pf.id, pf.nome
"""

VW_CLIENTE_RESUMO = """
CREATE OR REPLACE VIEW vw_cliente_resumo AS
SELECT c.id AS cliente_id, c.nome, c.telefone, c.email,
       c.eh_ativo, c.eh_bloqueado_online, c.qtd_faltas_consecutivas,
       (c.deletado_em IS NOT NULL)                        AS eh_excluido,
       (c.nome LIKE '[ANONIMIZADO-%')                     AS eh_anonimizado,
       c.criado_em,
       coalesce(ag.qtd_realizados, 0)                     AS qtd_realizados,
       coalesce(ag.qtd_faltas, 0)                         AS qtd_faltas,
       coalesce(ag.qtd_cancelados, 0)                     AS qtd_cancelados,
       ag.primeiro_realizado_em, ag.ultimo_realizado_em, ag.proximo_atendimento_em,
       coalesce(ag.valor_avulsos, 0)::numeric(12,2)       AS valor_avulsos,
       coalesce(pc.valor_pacotes, 0)::numeric(12,2)       AS valor_pacotes,
       (coalesce(ag.valor_avulsos, 0) + coalesce(pc.valor_pacotes, 0))::numeric(12,2) AS valor_total_gasto,
       coalesce(pc.qtd_pacotes_ativos, 0)                 AS qtd_pacotes_ativos,
       coalesce(ct.valor_saldo, 0)                        AS valor_saldo_carteira,
       EXISTS (SELECT 1 FROM prontuario p WHERE p.cliente_id = c.id
                  AND (p.alergias <> '' OR p.contraindicacoes <> '' OR p.historico_saude <> ''
                       OR p.medicamentos_uso <> '' OR p.observacoes_gerais <> '' OR p.respostas <> '{}'::jsonb))
                                                          AS tem_prontuario
  FROM cliente c
  LEFT JOIN LATERAL (
       SELECT count(*) FILTER (WHERE a.status = 'REALIZADO')                         AS qtd_realizados,
              count(*) FILTER (WHERE a.status = 'FALTOU')                            AS qtd_faltas,
              count(*) FILTER (WHERE a.status = 'CANCELADO')                         AS qtd_cancelados,
              min(a.data_hora_inicio) FILTER (WHERE a.status = 'REALIZADO')          AS primeiro_realizado_em,
              max(a.data_hora_inicio) FILTER (WHERE a.status = 'REALIZADO')          AS ultimo_realizado_em,
              min(a.data_hora_inicio) FILTER (WHERE a.status IN ('PENDENTE', 'AGENDADO', 'CONFIRMADO')
                                                AND a.data_hora_inicio >= now())      AS proximo_atendimento_em,
              sum(a.valor_cobrado) FILTER (WHERE a.status = 'REALIZADO' AND NOT a.eh_retorno
                                             AND a.valor_cobrado > 0 AND cs.id IS NULL) AS valor_avulsos
         FROM atendimento a
         LEFT JOIN consumo_sessao cs ON cs.atendimento_id = a.id
        WHERE a.cliente_id = c.id) ag ON true
  LEFT JOIN LATERAL (
       SELECT sum(cp.valor_pago - cp.valor_reembolsado) FILTER (WHERE cp.valor_pago > cp.valor_reembolsado)
                                                   AS valor_pacotes,
              count(*) FILTER (WHERE cp.status = 'ATIVO') AS qtd_pacotes_ativos
         FROM compra_pacote cp WHERE cp.cliente_id = c.id) pc ON true
  LEFT JOIN carteira ct ON ct.cliente_id = c.id
"""

# Estado vigente por titular/finalidade/canal (= services/consentimentos.estado_atual)
VW_CONSENTIMENTO_ATUAL = """
CREATE OR REPLACE VIEW vw_consentimento_atual AS
SELECT DISTINCT ON (cliente_id, finalidade, canal)
       cliente_id, finalidade, canal, eh_concedido, registrado_em, origem, id AS consentimento_id
  FROM consentimento
 ORDER BY cliente_id, finalidade, canal, registrado_em DESC, id DESC
"""

VW_PACOTE_SALDO = """
CREATE OR REPLACE VIEW vw_pacote_saldo AS
SELECT i.compra_pacote_id, cp.cliente_id, cp.pacote_nome, cp.status, cp.data_expiracao,
       i.id AS compra_pacote_item_id, i.procedimento_id, i.procedimento_nome,
       i.qtd_sessoes                                AS qtd_total,
       count(cs.id)                                 AS qtd_usada,
       greatest(i.qtd_sessoes - count(cs.id), 0)    AS qtd_restante
  FROM compra_pacote_item i
  JOIN compra_pacote cp ON cp.id = i.compra_pacote_id
  LEFT JOIN consumo_sessao cs ON cs.compra_pacote_item_id = i.id
 GROUP BY i.id, cp.id
"""

# o que tasks.job_limpeza_status_atendimentos so joga no log (D14: a equipe decide falta/realizado)
VW_ATENDIMENTO_SEM_DESFECHO = """
CREATE OR REPLACE VIEW vw_atendimento_sem_desfecho AS
SELECT a.id AS atendimento_id, a.profissional_id, a.cliente_id, a.status,
       a.data_hora_inicio, a.data_hora_fim
  FROM atendimento a
 WHERE a.status IN ('AGENDADO', 'CONFIRMADO')
   AND a.data_hora_fim < now() - interval '24 hours'
"""

FUNCOES_LEITURA = (
    ('fn_recorrencia_casa_dia', 'text, integer, text[], integer, date, date', FN_RECORRENCIA_CASA_DIA),
    ('fn_ocorrencias_recorrencia', 'text, timestamptz, interval, date, timestamptz, timestamptz',
     FN_OCORRENCIAS_RECORRENCIA),
    ('fn_horarios_livres', 'bigint, date, bigint, bigint, timestamptz', FN_HORARIOS_LIVRES),
    ('fn_saldo_pacote', 'bigint', FN_SALDO_PACOTE),
    ('fn_nps_periodo', 'timestamptz, timestamptz', FN_NPS_PERIODO),
    ('fn_saldo_comissao', 'bigint, timestamptz', FN_SALDO_COMISSAO),
    ('fn_tem_consentimento', 'bigint, text, text', FN_TEM_CONSENTIMENTO),
    ('fn_clientes_para_anonimizar', 'integer, integer, integer, timestamptz', FN_CLIENTES_PARA_ANONIMIZAR),
)
# ordem de criacao (vw_comissao_saldo le vw_comissao); o reverso derruba ao contrario
VIEWS = (
    ('vw_agenda_dia', VW_AGENDA_DIA),
    ('vw_faturamento_mensal', VW_FATURAMENTO_MENSAL),
    ('vw_nps_mensal', VW_NPS_MENSAL),
    ('vw_comissao', VW_COMISSAO),
    ('vw_comissao_saldo', VW_COMISSAO_SALDO),
    ('vw_cliente_resumo', VW_CLIENTE_RESUMO),
    ('vw_consentimento_atual', VW_CONSENTIMENTO_ATUAL),
    ('vw_pacote_saldo', VW_PACOTE_SALDO),
    ('vw_atendimento_sem_desfecho', VW_ATENDIMENTO_SEM_DESFECHO),
)

CRIAR = [
    *EXTENSOES,
    FN_FUSO_CLINICA,
    FN_DATA_LOCAL,
    FN_SEM_ACENTO,
    IX_CLIENTE_NOME_TRGM,
    *(sql for _nome, _args, sql in FUNCOES_LEITURA),
    *(sql for _nome, sql in VIEWS),
]

# extensoes ficam no reverso (como o btree_gist da 0035): CREATE EXTENSION IF NOT EXISTS e idempotente
REMOVER = [
    *(f'DROP VIEW IF EXISTS {nome}' for nome, _sql in reversed(VIEWS)),
    *(f'DROP FUNCTION IF EXISTS {nome}({args})' for nome, args, _sql in reversed(FUNCOES_LEITURA)),
    'DROP INDEX IF EXISTS ix_cliente_nome_trgm',
    'DROP FUNCTION IF EXISTS fn_sem_acento(text)',
    'DROP FUNCTION IF EXISTS fn_data_local(timestamptz)',
    'DROP FUNCTION IF EXISTS fn_fuso_clinica()',
]
