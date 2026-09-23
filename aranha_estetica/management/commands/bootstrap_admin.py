"""Cria/atualiza o ADMIN da clinica a partir do ambiente — idempotente.

Roda no pre-deploy logo apos o migrate:
    ADMIN_EMAIL=... ADMIN_PASSWORD=... [ADMIN_NOME=...] python manage.py bootstrap_admin

- Sem ADMIN_EMAIL/ADMIN_PASSWORD: nao faz nada.
- Usuario inexistente: cria com papel ADMIN.
- Usuario existente: garante papel ADMIN. A senha so e trocada se a conta
  estava desativada (reativacao — ex.: conta demo desligada pela migration),
  se nao tiver senha utilizavel, ou com ADMIN_PASSWORD_RESET=true / --reset-senha.
  Assim uma troca de senha feita no painel nao e desfeita a cada deploy.
- Senha fraca (validadores do Django) ou erro: avisa em stderr e sai com 0 —
  nunca derruba o pre-deploy (as migrations ja foram aplicadas).
"""
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction

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
                user = Usuario(email=Usuario.objects.normalize_email(email), nome=nome)

            trocar_senha = criar or reset or not user.ativo or not user.has_usable_password()
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

        if criar:
            msg = f'bootstrap_admin: admin {alvo} criado.'
        elif mudou:
            msg = f'bootstrap_admin: admin {alvo} atualizado' + (' (senha redefinida).' if trocar_senha else '.')
        else:
            msg = f'bootstrap_admin: admin {alvo} ja existe e esta ok (senha mantida).'
        self.stdout.write(self.style.SUCCESS(msg))
