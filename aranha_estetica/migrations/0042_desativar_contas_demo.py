# Auditoria pre-producao (pgmig-10): o seed.py antigo (historico do git, rodava
# em producao) criava admin@shivazen.com / 'admin123' e ana@shivazen.com /
# 'prof123'. A 0029 mapeia o perfil Administrador -> papel ADMIN, e a senha e
# publica no historico do repositorio. Aqui: se a conta ainda usa a senha
# padrao (check_password), desativa e invalida a senha. Conta cuja senha ja
# foi trocada fica intacta. O admin real e criado pelo comando bootstrap_admin
# (ADMIN_EMAIL/ADMIN_PASSWORD) no pre-deploy.
# Se a desativacao deixar o painel sem nenhum ADMIN ativo com senha e o env
# ADMIN_* nao estiver definido, avisa em stderr (log do deploy): sem isso o
# lockout era silencioso (bootstrap_admin sem env nao faz nada). O aviso sai
# so no COMMIT (transaction.on_commit): se uma migration posterior falhar, o
# migrate_atomico desfaz tudo e a conta demo continua como estava.
# O 2FA da conta demo (TOTP e codigos de backup) e apagado junto: em main
# /painel/seguranca/2fa/ aceitava quem logasse com a senha publica, e esse
# segundo fator sobreviveria a reativacao da conta pelo bootstrap_admin.
import os
import sys

from django.db import migrations, transaction

CONTAS_DEMO = (
    ('admin@shivazen.com', 'admin123'),
    ('ana@shivazen.com', 'prof123'),
)


def _avisar_se_sem_admin(Usuario, alias='default'):
    from django.contrib.auth.hashers import is_password_usable

    senhas = Usuario.objects.filter(papel='ADMIN', ativo=True).values_list('password', flat=True)
    if any(is_password_usable(s) for s in senhas):
        return
    if os.environ.get('ADMIN_EMAIL') and os.environ.get('ADMIN_PASSWORD'):
        return  # bootstrap_admin (pre-deploy, apos o migrate) cria/reativa o admin
    msg = (
        '0042: ATENCAO - conta demo desativada e o painel ficou sem ADMIN ativo. '
        'Defina ADMIN_EMAIL/ADMIN_PASSWORD (e ADMIN_NOME) no Railway e faca redeploy: '
        'o bootstrap_admin do pre-deploy cria o administrador.\n'
    )
    # so avisa se a transacao commitar (rollback = nada foi desativado)
    transaction.on_commit(lambda: sys.stderr.write(msg), using=alias)


def _remover_2fa(apps, usuario_pk):
    """Apaga TOTP e codigos de backup da conta. Devolve quantos devices havia."""
    removidos = 0
    for app_label, modelo in (('otp_totp', 'TOTPDevice'), ('otp_static', 'StaticDevice')):
        devices = apps.get_model(app_label, modelo).objects.filter(user_id=usuario_pk)
        removidos += devices.count()
        devices.delete()  # StaticToken cai junto (CASCADE)
    return removidos


def desativar_contas_demo(apps, schema_editor):
    from django.contrib.auth.hashers import check_password, make_password

    Usuario = apps.get_model('aranha_estetica', 'Usuario')
    LogAuditoria = apps.get_model('aranha_estetica', 'LogAuditoria')
    desativadas = 0
    for email, senha_padrao in CONTAS_DEMO:
        for usuario in Usuario.objects.filter(email__iexact=email):
            if not check_password(senha_padrao, usuario.password):
                continue
            Usuario.objects.filter(pk=usuario.pk).update(
                ativo=False,
                password=make_password(None),  # inutilizavel: reativar exige senha nova
            )
            devices_2fa = _remover_2fa(apps, usuario.pk)
            LogAuditoria.objects.create(
                acao='migration 0042: conta demo com senha padrao desativada',
                tabela='usuario',
                registro_id=usuario.pk,
                detalhes={'dispositivos_2fa_removidos': devices_2fa} if devices_2fa else None,
            )
            desativadas += 1
    if desativadas:
        _avisar_se_sem_admin(Usuario, schema_editor.connection.alias)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0041_on_delete_protect_indice_email_upper'),
        # devices OTP da conta demo; as migrations do otp dependem so do
        # AUTH_USER_MODEL (aranha_estetica __first__): sem ciclo
        ('otp_totp', '0001_initial'),
        ('otp_static', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(desativar_contas_demo, migrations.RunPython.noop),
    ]
