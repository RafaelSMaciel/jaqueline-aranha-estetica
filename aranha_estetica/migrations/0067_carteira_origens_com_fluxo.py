# Carteira: so as origens que tem fluxo (decisao "programa de indicacao"). Saem VALE_PRESENTE e OUTRO
# (nada as gerava) do TextChoices e do ck_movimento_carteira_origem, e o default 'OUTRO' da coluna:
# todo movimento diz de onde veio. O ledger e imutavel (tg_movimento_carteira_imutavel): linha com
# origem antiga nao da para converter — a migration aborta listando os ids (nao ha producao; guarda
# defensiva, como a da 0063).
from django.db import migrations, models

ORIGENS_REMOVIDAS = ('VALE_PRESENTE', 'OUTRO')


def conferir_origens(apps, schema_editor):
    MovimentoCarteira = apps.get_model('aranha_estetica', 'MovimentoCarteira')
    ids = list(MovimentoCarteira.objects.filter(origem__in=ORIGENS_REMOVIDAS).values_list('pk', flat=True))
    if ids:
        raise RuntimeError(
            f'movimento_carteira com origem {"/".join(ORIGENS_REMOVIDAS)} (ledger imutavel, sem conversao): ids {ids}'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0066_comissao_snapshot_regras'),
    ]

    operations = [
        migrations.RunPython(conferir_origens, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='movimentocarteira',
            name='ck_movimento_carteira_origem',
        ),
        migrations.AlterField(
            model_name='movimentocarteira',
            name='origem',
            field=models.CharField(choices=[('CANCELAMENTO', 'Devolução (atendimento não realizado)'), ('AJUSTE_MANUAL', 'Ajuste manual'), ('USO_AGENDAMENTO', 'Uso em atendimento'), ('CASHBACK_INDICACAO', 'Crédito por indicação'), ('CASHBACK_ESTORNO', 'Estorno do crédito por indicação')], max_length=30),
        ),
        migrations.AddConstraint(
            model_name='movimentocarteira',
            constraint=models.CheckConstraint(condition=models.Q(('origem__in', ['CANCELAMENTO', 'AJUSTE_MANUAL', 'USO_AGENDAMENTO', 'CASHBACK_INDICACAO', 'CASHBACK_ESTORNO'])), name='ck_movimento_carteira_origem'),
        ),
    ]
