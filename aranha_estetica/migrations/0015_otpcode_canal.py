"""Adiciona campo canal ao OtpCode (SMS/EMAIL)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app_shivazen', '0014_cliente_consent_channels'),
    ]

    operations = [
        migrations.AddField(
            model_name='otpcode',
            name='canal',
            field=models.CharField(
                max_length=10,
                choices=[('EMAIL', 'Email'), ('SMS', 'SMS')],
                default='EMAIL',
            ),
        ),
        migrations.AddField(
            model_name='otpcode',
            name='telefone',
            field=models.CharField(max_length=20, blank=True, null=True),
        ),
    ]
