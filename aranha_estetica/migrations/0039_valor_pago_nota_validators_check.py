# Auditoria SWE (severidade media): validacao em camada Python + CHECK de DB
#  - CompraPacote.valor_pago: MinValueValidator(0) + CheckConstraint nao-negativo
#  - AvaliacaoNPS.nota: Min/MaxValueValidator(0..10) alem do CHECK ja existente
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0038_remodelagem_fase6_triggers_collation_comments'),
    ]

    operations = [
        migrations.AlterField(
            model_name='comprapacote',
            name='valor_pago',
            field=models.DecimalField(
                decimal_places=2, max_digits=10,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AddConstraint(
            model_name='comprapacote',
            constraint=models.CheckConstraint(
                condition=models.Q(('valor_pago__gte', 0)),
                name='chk_compra_pacote_valor_pago',
            ),
        ),
        migrations.AlterField(
            model_name='avaliacaonps',
            name='nota',
            field=models.SmallIntegerField(
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(10),
                ],
            ),
        ),
    ]
