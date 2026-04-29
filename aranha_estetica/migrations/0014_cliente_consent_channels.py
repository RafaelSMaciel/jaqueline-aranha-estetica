"""Adiciona consents granulares (email marketing, whatsapp NPS) ao Cliente."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app_shivazen', '0013_add_missing_fk_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='consent_email_marketing',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='cliente',
            name='consent_email_marketing_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cliente',
            name='consent_email_marketing_ip',
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cliente',
            name='consent_whatsapp_nps',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='cliente',
            name='consent_whatsapp_nps_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cliente',
            name='consent_whatsapp_nps_ip',
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
    ]
