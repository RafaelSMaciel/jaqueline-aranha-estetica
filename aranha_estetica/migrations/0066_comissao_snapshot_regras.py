# Comissao explicavel (decisao "regras de comissao"):
#   1. movimento_comissao.valor_base_calculo + percentual_aplicado: snapshot do calculo no CREDITO
#      (como o item congelado da 0052). NULL nos derivados (CHECK) e nos creditos anteriores; comissao
#      nunca passa da base (CHECK). Sem DML: o ledger e append-only.
#   2. So Postgres (SQL em migrations/sql/v0066_comissao.py): tg_regra_comissao_validar — regra ja
#      usada nao muda profissional/procedimento/percentual/valor fixo (so desativa); vw_comissao com as
#      colunas do snapshot no fim. Reverso: tira o trigger e volta as views da 0057.
from django.db import migrations, models

from aranha_estetica.migrations.sql import v0066_comissao as sql


def _executar(schema_editor, comandos):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for comando in comandos:
        schema_editor.execute(comando, None)


def criar_logica(apps, schema_editor):
    _executar(schema_editor, sql.CRIAR)


def remover_logica(apps, schema_editor):
    _executar(schema_editor, sql.remover())


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0065_financeiro_competencia'),
    ]

    operations = [
        migrations.AlterModelTableComment(
            name='regracomissao',
            table_comment='Regra percentual XOR valor fixo por (profissional, procedimento); NULL = qualquer. Mais especifica vence; empate = atualizada por ultimo. Regra ja usada nao muda nem se exclui (tg_regra_comissao_validar, FK de movimento_comissao): desative e crie outra.',
        ),
        migrations.AddField(
            model_name='movimentocomissao',
            name='base_calculo',
            field=models.DecimalField(blank=True, db_column='valor_base_calculo', db_comment='CREDITO: base em que a regra incidiu (valor cobrado ou unitario do pacote), congelada.', decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='movimentocomissao',
            name='percentual_aplicado',
            field=models.DecimalField(blank=True, db_comment='CREDITO de regra percentual: o percentual aplicado (no maximo 100). NULL em regra de valor fixo.', decimal_places=2, max_digits=5, null=True),
        ),
        migrations.AddConstraint(
            model_name='movimentocomissao',
            constraint=models.CheckConstraint(condition=models.Q(('tipo', 'CREDITO'), models.Q(('base_calculo__isnull', True), ('percentual_aplicado__isnull', True)), _connector='OR'), name='ck_movimento_comissao_snapshot_so_credito'),
        ),
        migrations.AddConstraint(
            model_name='movimentocomissao',
            constraint=models.CheckConstraint(condition=models.Q(('base_calculo__isnull', True), models.Q(('base_calculo__gte', 0), ('valor__lte', models.F('base_calculo'))), _connector='OR'), name='ck_movimento_comissao_valor_ate_base_calculo'),
        ),
        migrations.AddConstraint(
            model_name='movimentocomissao',
            constraint=models.CheckConstraint(condition=models.Q(('percentual_aplicado__isnull', True), models.Q(('percentual_aplicado__gte', 0), ('percentual_aplicado__lte', 100)), _connector='OR'), name='ck_movimento_comissao_percentual_aplicado_0_100'),
        ),
        migrations.RunPython(criar_logica, remover_logica),
    ]
