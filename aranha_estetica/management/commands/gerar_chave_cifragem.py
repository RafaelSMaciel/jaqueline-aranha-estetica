"""Gera uma chave nova para CHAVES_CIFRAGEM (utils/cifragem).

Uso:
    python manage.py gerar_chave_cifragem

Imprime so a chave (base64 de 32 bytes). 1o deploy: CHAVES_CIFRAGEM=<chave>. Rotacao:
CHAVES_CIFRAGEM=<nova>,<antiga> + redeploy + `rotacionar_chave_cifragem` + tirar a antiga.
Guarde uma copia offline com a dona: sem a chave o prontuario cifrado nao abre.
"""
from django.core.management.base import BaseCommand

from aranha_estetica.utils.cifragem import gerar_chave


class Command(BaseCommand):
    help = 'Gera uma chave Fernet para CHAVES_CIFRAGEM (guarde uma copia offline).'
    requires_system_checks = []

    def handle(self, *args, **options):
        self.stdout.write(gerar_chave())
