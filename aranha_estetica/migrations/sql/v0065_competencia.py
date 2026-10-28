"""0065 — faturamento por competencia mensal (vw_faturamento_mensal) com o reembolso no mes do cancelamento.

CONGELADO: importado so pela 0065_financeiro_competencia. Mudanca = modulo novo + migration nova.

Espelho de services/financeiro.resumo_receita / ultimos_12_meses (paridade em tests/test_pg_paridade):
  - avulsos: como na 0057 (REALIZADO, nao retorno, valor > 0, sem consumo de pacote), mes do atendimento;
  - pacotes: valor_pago no mes da VENDA menos valor_reembolsado no mes do CANCELAMENTO (cancelado_em).
    valor_pacotes passa a ser esse liquido por competencia (pode ser negativo) e qtd_pacotes as vendas
    do mes; vendas e reembolsos tambem saem separados nas colunas novas, no fim.
A 0057 abatia o reembolso no mes da venda: um mes fechado mudava depois. Colunas antigas mantem nome,
tipo e ordem (quem lia a view continua lendo). DROP + CREATE nos dois sentidos: nada depende da view e o
reverso tem menos colunas (CREATE OR REPLACE nao remove coluna).
"""

VW_FATURAMENTO_MENSAL = """
CREATE VIEW vw_faturamento_mensal AS
WITH avulsos AS (
    SELECT date_trunc('month', a.data_hora_inicio AT TIME ZONE fn_fuso_clinica())::date AS mes,
           sum(a.valor_cobrado) AS valor, count(*) AS qtd
      FROM atendimento a
      LEFT JOIN consumo_sessao cs ON cs.atendimento_id = a.id
     WHERE a.status = 'REALIZADO' AND NOT a.eh_retorno AND a.valor_cobrado > 0 AND cs.id IS NULL
     GROUP BY 1
), vendas AS (
    SELECT date_trunc('month', cp.criado_em AT TIME ZONE fn_fuso_clinica())::date AS mes,
           sum(cp.valor_pago) AS valor, count(*) AS qtd
      FROM compra_pacote cp
     WHERE cp.valor_pago > 0
     GROUP BY 1
), reembolsos AS (
    SELECT date_trunc('month', cp.cancelado_em AT TIME ZONE fn_fuso_clinica())::date AS mes,
           sum(cp.valor_reembolsado) AS valor, count(*) AS qtd
      FROM compra_pacote cp
     WHERE cp.valor_reembolsado > 0
     GROUP BY 1
), meses AS (
    SELECT mes FROM avulsos UNION SELECT mes FROM vendas UNION SELECT mes FROM reembolsos
)
SELECT m.mes,
       coalesce(a.valor, 0)::numeric(12,2)                                       AS valor_atendimentos,
       coalesce(a.qtd, 0)                                                        AS qtd_atendimentos,
       (coalesce(v.valor, 0) - coalesce(r.valor, 0))::numeric(12,2)              AS valor_pacotes,
       coalesce(v.qtd, 0)                                                        AS qtd_pacotes,
       (coalesce(a.valor, 0) + coalesce(v.valor, 0) - coalesce(r.valor, 0))::numeric(12,2)
                                                                                 AS valor_total,
       CASE WHEN a.qtd > 0 THEN round(a.valor / a.qtd, 2) END                    AS valor_ticket_medio,
       coalesce(v.valor, 0)::numeric(12,2)                                       AS valor_vendas_pacotes,
       coalesce(r.valor, 0)::numeric(12,2)                                       AS valor_reembolsos_pacotes,
       coalesce(r.qtd, 0)                                                        AS qtd_reembolsos_pacotes
  FROM meses m
  LEFT JOIN avulsos a ON a.mes = m.mes
  LEFT JOIN vendas v ON v.mes = m.mes
  LEFT JOIN reembolsos r ON r.mes = m.mes
"""

COMMENT = (
    "COMMENT ON VIEW vw_faturamento_mensal IS 'Faturamento por competencia (mes local): avulsos no mes "
    "do atendimento; pacotes = vendas no mes da venda - reembolsos no mes do cancelamento (pode ser "
    "negativo). Espelho de services/financeiro.ultimos_12_meses.'"
)

CRIAR = ['DROP VIEW IF EXISTS vw_faturamento_mensal', VW_FATURAMENTO_MENSAL, COMMENT]


def remover():
    """Reverso: volta a view da 0057 (reembolso no mes da venda)."""
    from . import v0057_leitura

    return ['DROP VIEW IF EXISTS vw_faturamento_mensal', v0057_leitura.VW_FATURAMENTO_MENSAL]
