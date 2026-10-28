# Financeiro por competencia mensal: o reembolso de pacote deduz a receita do mes em que aconteceu,
# nao do mes da venda (mes fechado nao muda depois).
#   1. compra_pacote.cancelado_em + ck_compra_pacote_cancelado_em_coerente (CANCELADO <=> instante
#      preenchido) + ck_compra_pacote_reembolso_so_cancelado + ix parcial p/ os reembolsos do mes.
#      Dados (nao ha producao: so guarda defensiva): compra ja CANCELADA ganha cancelado_em =
#      atualizado_em (o melhor instante conhecido); reembolso em compra nao cancelada aborta com os ids.
#   2. compra_pacote_item.valor_unitario: comentario do rateio ponderado pelo preco de tabela (o
#      calculo e Python, na venda; itens ja congelados nao mudam).
#   3. So Postgres: vw_faturamento_mensal por competencia (SQL em migrations/sql/v0065_competencia.py;
#      reverso volta a da 0057).
import aranha_estetica.db.indices
from django.db import migrations, models

from aranha_estetica.migrations.sql import v0065_competencia as sql


def preencher_cancelado_em(apps, schema_editor):
    CompraPacote = apps.get_model('aranha_estetica', 'CompraPacote')
    invalidas = list(CompraPacote.objects.filter(valor_reembolsado__gt=0).exclude(status='CANCELADO')
                     .values_list('pk', flat=True))
    if invalidas:
        raise RuntimeError(f'compra_pacote com reembolso sem estar CANCELADA (corrija antes): ids {invalidas}')
    CompraPacote.objects.filter(status='CANCELADO', cancelado_em__isnull=True).update(
        cancelado_em=models.F('atualizado_em'))


def _executar(schema_editor, comandos):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for comando in comandos:
        schema_editor.execute(comando, None)


def criar_view(apps, schema_editor):
    _executar(schema_editor, sql.CRIAR)


def remover_view(apps, schema_editor):
    _executar(schema_editor, sql.remover())


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0064_carteira_rotulos'),
    ]

    operations = [
        migrations.AlterModelTableComment(
            name='comprapacote',
            table_comment='Venda de pacote (registro fiscal, PROTECT). Receita por competencia (D20): valor_pago no mes da venda; valor_reembolsado deduz no mes do cancelamento (cancelado_em).',
        ),
        migrations.AddField(
            model_name='comprapacote',
            name='cancelado_em',
            field=models.DateTimeField(blank=True, db_comment='Instante do cancelamento: o reembolso deduz a receita do mes deste instante (competencia).', null=True),
        ),
        migrations.AlterField(
            model_name='comprapacoteitem',
            name='valor_unitario',
            field=models.DecimalField(db_comment='Valor de 1 sessao: valor pago rateado pelo preco de tabela de cada procedimento no dia da venda (uniforme sem preco) - base da comissao da sessao de pacote (D20). 0 = reconstruido pela 0052.', decimal_places=2, max_digits=10),
        ),
        migrations.RunPython(preencher_cancelado_em, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name='comprapacote',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('cancelado_em__isnull', False)), fields=['cancelado_em'], name='ix_compra_pacote_cancelado_em_parcial'),
        ),
        migrations.AddConstraint(
            model_name='comprapacote',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('status', 'CANCELADO'), ('cancelado_em__isnull', False)), models.Q(models.Q(('status', 'CANCELADO'), _negated=True), ('cancelado_em__isnull', True)), _connector='OR'), name='ck_compra_pacote_cancelado_em_coerente'),
        ),
        migrations.AddConstraint(
            model_name='comprapacote',
            constraint=models.CheckConstraint(condition=models.Q(('valor_reembolsado', 0), ('status', 'CANCELADO'), _connector='OR'), name='ck_compra_pacote_reembolso_so_cancelado'),
        ),
        migrations.RunPython(criar_view, remover_view),
    ]
