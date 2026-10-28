# Comissao: no maximo 1 CREDITO ATIVO (nao estornado) por atendimento, em vez de 1 CREDITO para
# sempre. O UNIQUE parcial uq_movimento_comissao_credito_por_atendimento impedia corrigir o valor de
# um atendimento realizado (estorno + credito novo) e refazer uma realizacao desfeita — o caso
# real (Realizado marcado por engano) ficava sem saida. A garantia vai p/ o gatilho que ja valida o
# ledger (fn_movimento_comissao_validar): CREDITO novo trava a linha do atendimento e e recusado se
# houver outro sem ESTORNO. O resto da funcao e o da 0053. Espelho Python: MovimentoComissao.save.
from django.db import migrations

SQL_FUNCAO = """
CREATE OR REPLACE FUNCTION fn_movimento_comissao_validar() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_origem movimento_comissao%ROWTYPE;
BEGIN
    IF NEW.tipo = 'CREDITO' THEN
        PERFORM 1 FROM atendimento WHERE id = NEW.atendimento_id FOR UPDATE;   -- serializa creditos
        IF EXISTS (
               SELECT 1 FROM movimento_comissao c
                WHERE c.atendimento_id = NEW.atendimento_id AND c.tipo = 'CREDITO'
                  AND NOT EXISTS (SELECT 1 FROM movimento_comissao e
                                   WHERE e.movimento_comissao_origem_id = c.id AND e.tipo = 'ESTORNO')) THEN
            RAISE EXCEPTION 'movimento_comissao: atendimento % ja tem credito ativo (estorne antes)', NEW.atendimento_id
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO v_origem FROM movimento_comissao WHERE id = NEW.movimento_comissao_origem_id
       FOR UPDATE;                                -- serializa PAGAMENTO x ESTORNO do mesmo credito
    IF v_origem.id IS NULL THEN
        RETURN NEW;                               -- a FK (deferida) acusa
    END IF;
    IF v_origem.tipo <> 'CREDITO' THEN
        RAISE EXCEPTION 'movimento_comissao: % deve referenciar um CREDITO (origem %)', NEW.tipo, v_origem.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.profissional_id, NEW.atendimento_id) IS DISTINCT FROM (v_origem.profissional_id, v_origem.atendimento_id)
       OR NEW.valor <> v_origem.valor THEN
        RAISE EXCEPTION 'movimento_comissao: % deve repetir profissional, atendimento e valor do credito %', NEW.tipo, v_origem.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.tipo = 'PAGAMENTO' AND EXISTS (
           SELECT 1 FROM movimento_comissao
            WHERE movimento_comissao_origem_id = v_origem.id AND tipo = 'ESTORNO') THEN
        RAISE EXCEPTION 'movimento_comissao: credito % estornado nao pode ser pago', v_origem.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def aplicar(apps, schema_editor):
    if _pg(schema_editor):
        schema_editor.execute(SQL_FUNCAO, None)


def reverter(apps, schema_editor):
    if _pg(schema_editor):
        import importlib
        m0053 = importlib.import_module('aranha_estetica.migrations.0053_comissao_ledger')
        schema_editor.execute(m0053.SQL_FUNCAO_VALIDAR, None)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0062_habilitacao_obrigatoria'),
    ]

    operations = [
        migrations.RunPython(aplicar, reverter),
        migrations.RemoveConstraint(
            model_name='movimentocomissao',
            name='uq_movimento_comissao_credito_por_atendimento',
        ),
    ]
