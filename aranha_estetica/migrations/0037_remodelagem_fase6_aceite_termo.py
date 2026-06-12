# Remodelagem v2.1 — Fase 6.2 (docs/specs/remodelagem-banco-v2.md secao 3)
# Unifica aceite_privacidade + assinatura_termo_procedimento -> aceite_termo.
# A distincao (LGPD vs procedimento) vive em versao_termo.tipo; atendimento
# so e preenchido em termos de procedimento. -1 tabela.

import django.db.models.deletion
from django.db import migrations, models


def copiar_aceites(apps, schema_editor):
    AceitePrivacidade = apps.get_model('aranha_estetica', 'AceitePrivacidade')
    Assinatura = apps.get_model('aranha_estetica', 'AssinaturaTermoProcedimento')
    AceiteTermo = apps.get_model('aranha_estetica', 'AceiteTermo')

    for a in AceitePrivacidade.objects.all():
        AceiteTermo.objects.get_or_create(
            cliente_id=a.cliente_id,
            versao_termo_id=a.versao_termo_id,
            defaults={'ip': a.ip or None, 'criado_em': a.criado_em},
        )
    for a in Assinatura.objects.all():
        AceiteTermo.objects.get_or_create(
            cliente_id=a.cliente_id,
            versao_termo_id=a.versao_termo_id,
            defaults={
                'atendimento_id': a.atendimento_id,
                'ip': a.ip or None,
                'criado_em': a.criado_em,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0036_remodelagem_fase5_prontuario_jsonb'),
    ]

    operations = [
        migrations.CreateModel(
            name='AceiteTermo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, default='', max_length=500)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atendimento', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='aranha_estetica.atendimento')),
                ('cliente', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='aceites', to='aranha_estetica.cliente')),
                ('versao_termo', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, to='aranha_estetica.versaotermo')),
            ],
            options={
                'db_table': 'aceite_termo',
                'managed': True,
                'constraints': [
                    models.UniqueConstraint(fields=('cliente', 'versao_termo'), name='uniq_aceite_cliente_versao'),
                ],
                'indexes': [
                    models.Index(fields=['cliente', '-criado_em'], name='idx_aceite_cli_data'),
                ],
            },
        ),
        migrations.RunPython(copiar_aceites, migrations.RunPython.noop),
        migrations.DeleteModel(name='AceitePrivacidade'),
        migrations.DeleteModel(name='AssinaturaTermoProcedimento'),
    ]
