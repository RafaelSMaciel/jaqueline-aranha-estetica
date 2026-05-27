"""Altera default do campo canal de OtpCode: EMAIL -> SMS.

OTP migrou para SMS exclusivo via Zenvia (sem fallback email).
Default antigo era vestigio do canal email descontinuado.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0025_movimentocomissao_regracomissao_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='otpcode',
            name='canal',
            field=models.CharField(
                max_length=10,
                choices=[('EMAIL', 'Email'), ('SMS', 'SMS')],
                default='SMS',
            ),
        ),
    ]
