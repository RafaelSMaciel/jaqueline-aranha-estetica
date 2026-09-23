"""Cria/atualiza o ADMIN da clinica a partir do ambiente — idempotente.

Roda no pre-deploy logo apos o migrate:
    ADMIN_EMAIL=... ADMIN_PASSWORD=... [ADMIN_NOME=...] python manage.py bootstrap_admin

- Sem ADMIN_EMAIL/ADMIN_PASSWORD: nao faz nada.
- Usuario inexistente: cria com papel ADMIN e e-mail em minusculas (o login
  compara o e-mail exato; o painel tambem grava em minusculas).
- Usuario existente: garante papel ADMIN. A senha so e trocada se a conta
  estava desativada (reativacao — ex.: conta demo desligada pela migration),
  se nao tiver senha utilizavel, ou com ADMIN_PASSWORD_RESET=true / --reset-senha.
  Assim uma troca de senha feita no painel nao e desfeita a cada deploy.
- Conta desativada ou rebaixada no painel com OUTRO admin ativo: mantida como
  esta (decisao da equipe — saida da pessoa, suspeita de invasao). So volta a
  ADMIN ativo em lockout (nenhum admin utilizavel) ou com reset explicito.
- Reativacao (conta desativada ou sem senha utilizavel): remove os devices de
  2FA da conta — o TOTP/codigos de backup cadastrados por quem usou a senha
  antiga (ex.: senha publica da conta demo) nao podem valer p/ o dono. O
  proximo login cai no cadastro obrigatorio de 2FA. Reset com a conta ativa
  mantem o 2FA.
- ADMIN_PASSWORD fica em texto puro na env: tire-a do Railway depois do 1o
  deploy (sem ela o comando nao faz nada).
- Senha fraca (validadores do Django) ou erro: avisa em stderr e sai com 0 —
  nunca derruba o pre-deploy (as migrations ja foram aplicadas).
- Ao final, se nao sobrou nenhum ADMIN ativo com senha utilizavel (ex.: a
  migration 0042 desligou a conta demo e a env nao foi definida), escreve ERRO
  em stderr: o painel ficaria inacessivel sem nenhum aviso no log do deploy.
"""
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction

from aranha_estetica.checks import ha_admin_utilizavel
from aranha_estetica.utils.audit import registrar_log
from aranha_estetica.utils.security import mask_email


class Command(BaseCommand):
    help = 'Cria/atualiza o usuario ADMIN a partir de ADMIN_EMAIL/ADMIN_PASSWORD (idempotente).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset-senha', action='store_true',
            help='Redefine a senha de um admin existente com ADMIN_PASSWORD.',
        )

    def handle(self, *args, **options):
        try:
            self._bootstrap(options)
        except Exception as exc:  # noqa: BLE001 — pre-deploy nao pode falhar por isso
            self.stderr.write(f'bootstrap_admin: falhou ({exc.__class__.__name__}: {exc}).')
        self._conferir_admin()

    def _conferir_admin(self):
        try:
            ok = ha_admin_utilizavel()
        except Exception as exc:  # noqa: BLE001 — idem: so avisa
            self.stderr.write(f'bootstrap_admin: nao conferiu os admins ({exc.__class__.__name__}: {exc}).')
            return
        if not ok:
            self.stderr.write(
                'bootstrap_admin: ERRO — painel sem administrador ativo (nenhum ADMIN ativo com '
                'senha utilizavel); defina ADMIN_EMAIL/ADMIN_PASSWORD no Railway e faca redeploy.'
            )

    def _bootstrap(self, options):
        email = (os.environ.get('ADMIN_EMAIL') or '').strip()
        senha = os.environ.get('ADMIN_PASSWORD') or ''
        if not email or not senha:
            self.stdout.write('bootstrap_admin: ADMIN_EMAIL/ADMIN_PASSWORD ausentes — nada a fazer.')
            return

        nome = (os.environ.get('ADMIN_NOME') or '').strip() or 'Administrador'
        reset = options['reset_senha'] or (
            (os.environ.get('ADMIN_PASSWORD_RESET') or '').strip().lower() == 'true'
        )
        Usuario = get_user_model()
        alvo = mask_email(email)

        with transaction.atomic():
            user = Usuario.objects.select_for_update().filter(email__iexact=email).first()
            criar = user is None
            if criar:
                # normalize_email so baixava o dominio: 'Dona@Clinica.com' virava
                # 'Dona@clinica.com' e o login com 'dona@clinica.com' falhava.
                user = Usuario(email=email.lower(), nome=nome)
            elif (not reset and (not user.ativo or user.papel != Usuario.PAPEL_ADMIN)
                    and ha_admin_utilizavel()):
                self.stderr.write(
                    f'bootstrap_admin: {alvo} esta desativado ou sem papel ADMIN no painel e ha '
                    'outro admin ativo: mantido. Use ADMIN_PASSWORD_RESET=true p/ restaurar.'
                )
                return

            reativando = not criar and (not user.ativo or not user.has_usable_password())
            trocar_senha = criar or reset or reativando
            if trocar_senha:
                try:
                    validate_password(senha, user)
                except ValidationError as exc:
                    self.stderr.write(
                        f'bootstrap_admin: ADMIN_PASSWORD recusada ({"; ".join(exc.messages)}). '
                        f'Admin {alvo} NAO foi criado/reativado.'
                    )
                    return
                user.set_password(senha)

            mudou = trocar_senha
            if user.papel != Usuario.PAPEL_ADMIN:
                user.papel = Usuario.PAPEL_ADMIN
                mudou = True
            if not user.ativo:
                user.ativo = True
                mudou = True
            if mudou:
                user.save()
            if reativando and self._remover_2fa(user):
                registrar_log(None, 'bootstrap_admin: 2FA removido na reativacao', 'usuario', user.pk)
                self.stdout.write(
                    f'bootstrap_admin: 2FA antigo de {alvo} removido (cadastre de novo no 1o login).'
                )

        if criar:
            msg = f'bootstrap_admin: admin {alvo} criado.'
        elif mudou:
            msg = f'bootstrap_admin: admin {alvo} atualizado' + (' (senha redefinida).' if trocar_senha else '.')
        else:
            msg = f'bootstrap_admin: admin {alvo} ja existe e esta ok (senha mantida).'
        self.stdout.write(self.style.SUCCESS(msg))

    @staticmethod
    def _remover_2fa(user) -> bool:
        """Apaga todos os devices OTP da conta (TOTP, backup...). True se havia algum."""
        from django_otp import devices_for_user

        devices = list(devices_for_user(user, confirmed=None))
        for device in devices:
            device.delete()
        return bool(devices)
