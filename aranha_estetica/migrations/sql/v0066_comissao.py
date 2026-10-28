"""0066 — comissao explicavel: regra usada nao muda (tg_regra_comissao_validar) e vw_comissao com o
snapshot do calculo (valor_base_calculo, percentual_aplicado) do CREDITO.

CONGELADO: importado so pela 0066_comissao_snapshot_regras. Mudanca = modulo novo + migration nova.

Espelho Python: RegraComissao.save (MSG_REGRA_USADA) e MovimentoComissao.objects.creditos_com_status().
"""

# Regra ja citada por um movimento_comissao: profissional, procedimento, percentual e valor fixo nao
# mudam (o CREDITO antigo deixaria de ser explicavel). eh_ativo muda (desativar e o caminho).
FN_REGRA_COMISSAO_VALIDAR = """
CREATE OR REPLACE FUNCTION fn_regra_comissao_validar() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
BEGIN
    IF (NEW.profissional_id, NEW.procedimento_id, NEW.percentual, NEW.valor_fixo)
           IS DISTINCT FROM (OLD.profissional_id, OLD.procedimento_id, OLD.percentual, OLD.valor_fixo)
       AND EXISTS (SELECT 1 FROM movimento_comissao WHERE regra_comissao_id = OLD.id) THEN
        RAISE EXCEPTION 'regra_comissao: regra % ja usada em comissao nao muda (desative e crie outra)', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
"""

TG_REGRA_COMISSAO_VALIDAR = """
CREATE TRIGGER tg_regra_comissao_validar
    BEFORE UPDATE ON regra_comissao
    FOR EACH ROW EXECUTE FUNCTION fn_regra_comissao_validar()
"""

# = 0057 + as duas colunas do snapshot no fim (CREATE OR REPLACE so acrescenta coluna no fim)
VW_COMISSAO = """
CREATE OR REPLACE VIEW vw_comissao AS
SELECT c.id AS movimento_comissao_id, c.profissional_id, c.atendimento_id, c.regra_comissao_id,
       c.valor, c.criado_em,
       p.criado_em AS pago_em,
       e.criado_em AS estornado_em,
       CASE WHEN e.id IS NOT NULL THEN 'ESTORNADA'
            WHEN p.id IS NOT NULL THEN 'PAGA'
            ELSE 'PENDENTE' END AS status,
       c.valor_base_calculo,
       c.percentual_aplicado
  FROM movimento_comissao c
  LEFT JOIN movimento_comissao p ON p.movimento_comissao_origem_id = c.id AND p.tipo = 'PAGAMENTO'
  LEFT JOIN movimento_comissao e ON e.movimento_comissao_origem_id = c.id AND e.tipo = 'ESTORNO'
 WHERE c.tipo = 'CREDITO'
"""

CRIAR = [FN_REGRA_COMISSAO_VALIDAR, TG_REGRA_COMISSAO_VALIDAR, VW_COMISSAO]


def remover():
    """Reverso: tira o trigger e volta vw_comissao/vw_comissao_saldo da 0057 (a view nova tem mais
    colunas: DROP e CREATE, na ordem das dependencias)."""
    from . import v0057_leitura

    return [
        'DROP TRIGGER IF EXISTS tg_regra_comissao_validar ON regra_comissao',
        'DROP FUNCTION IF EXISTS fn_regra_comissao_validar()',
        'DROP VIEW IF EXISTS vw_comissao_saldo',
        'DROP VIEW IF EXISTS vw_comissao',
        v0057_leitura.VW_COMISSAO,
        v0057_leitura.VW_COMISSAO_SALDO,
    ]
