# Auditoria pre-producao (P3_db):
#  - on_delete PROTECT onde SET_NULL/CASCADE colidia com o trigger do ledger
#    imutavel (0038: UPDATE/DELETE em movimento_carteira -> InternalError 500)
#    ou apagava historico clinico/financeiro (prontuario, anotacao, anamnese,
#    compra de pacote, carteira). on_delete nao gera DDL (so estado/Collector).
#  - idx_cliente_email_upper: email__iexact gera UPPER(email) = UPPER(%s); o
#    UNIQUE parcial LOWER(email) nao atende esse lookup.
#  - uniq_consumo_por_atendimento removida: duplicava o UNIQUE do OneToOneField.

import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0040_avaliacao_nps_publicacao_depoimento'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='consumosessao',
            name='uniq_consumo_por_atendimento',
        ),
        migrations.AlterField(
            model_name='anotacaosessao',
            name='atendimento',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='anotacoes', to='aranha_estetica.atendimento'),
        ),
        migrations.AlterField(
            model_name='carteira',
            name='cliente',
            field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='carteira', to='aranha_estetica.cliente'),
        ),
        migrations.AlterField(
            model_name='comprapacote',
            name='cliente',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pacotes_comprados', to='aranha_estetica.cliente'),
        ),
        migrations.AlterField(
            model_name='movimentocarteira',
            name='atendimento',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='aranha_estetica.atendimento'),
        ),
        migrations.AlterField(
            model_name='movimentocarteira',
            name='carteira',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='movimentos', to='aranha_estetica.carteira'),
        ),
        migrations.AlterField(
            model_name='movimentocarteira',
            name='usuario',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='prontuario',
            name='cliente',
            field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to='aranha_estetica.cliente'),
        ),
        migrations.AlterField(
            model_name='respostaanamnese',
            name='atendimento',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='aranha_estetica.atendimento'),
        ),
        migrations.AlterField(
            model_name='respostaanamnese',
            name='cliente',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='aranha_estetica.cliente'),
        ),
        migrations.AddIndex(
            model_name='cliente',
            index=models.Index(django.db.models.functions.text.Upper('email'), name='idx_cliente_email_upper'),
        ),
    ]
