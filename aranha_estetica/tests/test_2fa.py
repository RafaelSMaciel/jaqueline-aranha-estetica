"""Testes 2FA — django-otp/two_factor (admin Django) + comando setup_2fa.

Convertido de pytest puro para django.test.TestCase: o runner do
`manage.py test` nao coletava as funcoes @pytest.mark.django_db.
Fluxos do painel (challenge, cadastro obrigatorio, bypass) em test_auth_equipe.py.
"""
from io import StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice


class DoisFatoresConfigTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff_user = User.objects.create_user(
            email='admin2fa@test.com', password='senha123', nome='Admin 2FA',
            papel=User.PAPEL_ADMIN,
        )

    def _setup_2fa(self, *args):
        call_command('setup_2fa', *args, stdout=StringIO())

    def test_apps_e_middleware_carregados(self):
        self.assertIn('django_otp', settings.INSTALLED_APPS)
        self.assertIn('django_otp.plugins.otp_totp', settings.INSTALLED_APPS)
        self.assertIn('two_factor', settings.INSTALLED_APPS)
        self.assertIn('django_otp.middleware.OTPMiddleware', settings.MIDDLEWARE)

    def test_admin_otp_required_substituido(self):
        """Admin Django (/django-admin-sv/) deve estar com AdminSiteOTPRequired."""
        from django.contrib import admin
        from two_factor.admin import AdminSiteOTPRequired
        self.assertTrue(
            isinstance(admin.site, AdminSiteOTPRequired)
            or admin.site.__class__ == AdminSiteOTPRequired
        )

    def test_setup_2fa_command_cria_device(self):
        self._setup_2fa('admin2fa@test.com')
        self.assertTrue(TOTPDevice.objects.filter(user=self.staff_user, confirmed=True).exists())
        backup = StaticDevice.objects.filter(user=self.staff_user, name='backup').first()
        self.assertIsNotNone(backup)
        self.assertEqual(backup.token_set.count(), 10)

    def test_setup_2fa_command_idempotente(self):
        self._setup_2fa('admin2fa@test.com')
        with self.assertRaises(CommandError):
            self._setup_2fa('admin2fa@test.com')
        # com --force recria
        self._setup_2fa('admin2fa@test.com', '--force')
        self.assertEqual(TOTPDevice.objects.filter(user=self.staff_user).count(), 1)

    def test_setup_2fa_email_inexistente(self):
        with self.assertRaises(CommandError):
            self._setup_2fa('naoexiste@test.com')

    def test_login_publico_e_booking_nao_afetados(self):
        """Endpoints publicos nao devem exigir 2FA."""
        client = Client()
        self.assertEqual(client.get('/').status_code, 200)
        self.assertIn(client.get('/agendamento/').status_code, (200, 302))
