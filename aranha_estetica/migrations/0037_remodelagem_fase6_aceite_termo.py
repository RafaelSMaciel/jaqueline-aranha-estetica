# Remodelagem v2.1 — Fase 6.2 (docs/specs/remodelagem-banco-v2.md secao 3)
# Unifica aceite_privacidade + assinatura_termo_procedimento -> aceite_termo.
# A distincao (LGPD vs procedimento) vive em versao_termo.tipo; atendimento
# so e preenchido em termos de procedimento. -1 tabela.

#
# Auditoria pre-producao (editada in-place: prod nunca aplicou a 0037):
#  - criado_em e auto_now_add: o pre_save sobrescrevia a data no INSERT e
#    todo aceite migrado ficava com a data do deploy (perda da prova legal do
#    consentimento). A data original e restaurada com .update() (sem pre_save);
#  - ip antigo era CharField(45) cru (1o item do X-Forwarded-For): valor que
#    nao e IP ('unknown', 'ip:porta') abortava no inet -> vira NULL.

import ipaddress

import django.db.models.deletion
from django.db import migrations, models


def _ip_ok(valor):
    valor = (valor or '').strip()
    if not valor:
        return None
    try:
        return str(ipaddress.ip_address(valor))
    except ValueError:
        return None


def copiar_aceites(apps, schema_editor):
    AceitePrivacidade = apps.get_model('aranha_estetica', 'AceitePrivacidade')
    Assinatura = apps.get_model('aranha_estetica', 'AssinaturaTermoProcedimento')
    AceiteTermo = apps.get_model('aranha_estetica', 'AceiteTermo')

    origens = list(AceitePrivacidade.objects.order_by('pk')) + list(Assinatura.objects.order_by('pk'))
    for a in origens:
        obj, criado = AceiteTermo.objects.get_or_create(
            cliente_id=a.cliente_id,
            versao_termo_id=a.versao_termo_id,
            defaults={
                'atendimento_id': getattr(a, 'atendimento_id', None),
                'ip': _ip_ok(a.ip),
            },
        )
        if criado and a.criado_em:
            # .update() nao passa pelo pre_save: preserva a data real do aceite
            AceiteTermo.objects.filter(pk=obj.pk).update(criado_em=a.criado_em)
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


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
