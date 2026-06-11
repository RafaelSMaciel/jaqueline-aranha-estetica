# Remodelagem v2.1 — Fase 1c (docs/specs/remodelagem-banco-v2.md)
# RBAC granular nunca consultado (Perfil/Funcionalidade/PerfilFuncionalidade)
# vira Usuario.papel CharField + CHECK. Data migration preserva o papel real
# de cada usuario existente a partir de perfil.nome.

from django.db import migrations, models


def copiar_perfil_para_papel(apps, schema_editor):
    Usuario = apps.get_model('aranha_estetica', 'Usuario')
    mapa = {
        'Administrador': 'ADMIN',
        'Profissional': 'PROFISSIONAL',
        'Recepcionista': 'RECEPCAO',
    }
    for usuario in Usuario.objects.select_related('perfil').all():
        nome_perfil = usuario.perfil.nome if usuario.perfil_id else ''
        usuario.papel = mapa.get(nome_perfil, 'RECEPCAO')
        usuario.save(update_fields=['papel'])


def reverter_papel_para_perfil(apps, schema_editor):
    Usuario = apps.get_model('aranha_estetica', 'Usuario')
    Perfil = apps.get_model('aranha_estetica', 'Perfil')
    mapa = {
        'ADMIN': 'Administrador',
        'PROFISSIONAL': 'Profissional',
        'RECEPCAO': 'Recepcionista',
    }
    for usuario in Usuario.objects.all():
        perfil, _ = Perfil.objects.get_or_create(nome=mapa.get(usuario.papel, 'Recepcionista'))
        usuario.perfil = perfil
        usuario.save(update_fields=['perfil'])


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0028_remodelagem_fase1b_otp_unico'),
    ]

    operations = [
        # 1. campo novo
        migrations.AddField(
            model_name='usuario',
            name='papel',
            field=models.CharField(
                choices=[
                    ('ADMIN', 'Administrador'),
                    ('PROFISSIONAL', 'Profissional'),
                    ('RECEPCAO', 'Recepcao'),
                ],
                default='RECEPCAO',
                max_length=20,
            ),
        ),
        # 2. copia dados do perfil
        migrations.RunPython(copiar_perfil_para_papel, reverter_papel_para_perfil),
        # 3. CHECK no banco
        migrations.AddConstraint(
            model_name='usuario',
            constraint=models.CheckConstraint(
                check=models.Q(papel__in=['ADMIN', 'PROFISSIONAL', 'RECEPCAO']),
                name='chk_usuario_papel',
            ),
        ),
        # 4. derruba FK e os 3 models de RBAC (filho join primeiro)
        migrations.RemoveField(model_name='usuario', name='perfil'),
        migrations.DeleteModel(name='PerfilFuncionalidade'),
        migrations.DeleteModel(name='Perfil'),
        migrations.DeleteModel(name='Funcionalidade'),
    ]
