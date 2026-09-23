# Auditoria pre-producao (pgmig-10): o seed.py antigo (historico do git, rodava
# em producao) criava admin@shivazen.com / 'admin123' e ana@shivazen.com /
# 'prof123'. A 0029 mapeia o perfil Administrador -> papel ADMIN, e a senha e
# publica no historico do repositorio. Aqui: se a conta ainda usa a senha
# padrao (check_password), desativa e invalida a senha. Conta cuja senha ja
# foi trocada fica intacta. O admin real e criado pelo comando bootstrap_admin
# (ADMIN_EMAIL/ADMIN_PASSWORD) no pre-deploy.

from django.db import migrations

CONTAS_DEMO = (
    ('admin@shivazen.com', 'admin123'),
    ('ana@shivazen.com', 'prof123'),
)


def desativar_contas_demo(apps, schema_editor):
    from django.contrib.auth.hashers import check_password, make_password

    Usuario = apps.get_model('aranha_estetica', 'Usuario')
    LogAuditoria = apps.get_model('aranha_estetica', 'LogAuditoria')
    for email, senha_padrao in CONTAS_DEMO:
        for usuario in Usuario.objects.filter(email__iexact=email):
            if not check_password(senha_padrao, usuario.password):
                continue
            Usuario.objects.filter(pk=usuario.pk).update(
                ativo=False,
                password=make_password(None),  # inutilizavel: reativar exige senha nova
            )
            LogAuditoria.objects.create(
                acao='migration 0042: conta demo com senha padrao desativada',
                tabela='usuario',
                registro_id=usuario.pk,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0041_on_delete_protect_indice_email_upper'),
    ]

    operations = [
        migrations.RunPython(desativar_contas_demo, migrations.RunPython.noop),
    ]
