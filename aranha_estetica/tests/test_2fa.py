"""Testes 2FA — django-two-factor-auth integration."""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice


@pytest.fixture
def staff_user(db):
    from aranha_estetica.models import Perfil
    User = get_user_model()
    perfil, _ = Perfil.objects.get_or_create(nome='Administrador')
    u = User.objects.create_user(
        email='admin2fa@test.com', password='senha123', nome='Admin 2FA'
    )
    u.perfil = perfil
    u.save()
    return u


@pytest.mark.django_db
def test_apps_e_middleware_carregados():
    from django.conf import settings
    assert 'django_otp' in settings.INSTALLED_APPS
    assert 'django_otp.plugins.otp_totp' in settings.INSTALLED_APPS
    assert 'two_factor' in settings.INSTALLED_APPS
    assert 'django_otp.middleware.OTPMiddleware' in settings.MIDDLEWARE


@pytest.mark.django_db
def test_admin_otp_required_substituido(client, staff_user):
    """Admin Django (/django-admin-sv/) deve estar com AdminSiteOTPRequired."""
    from django.contrib import admin
    from two_factor.admin import AdminSiteOTPRequired
    assert isinstance(admin.site, AdminSiteOTPRequired) or admin.site.__class__ == AdminSiteOTPRequired


@pytest.mark.django_db
def test_setup_2fa_command_cria_device(staff_user):
    from django.core.management import call_command
    call_command('setup_2fa', 'admin2fa@test.com')
    assert TOTPDevice.objects.filter(user=staff_user, confirmed=True).exists()
    backup = StaticDevice.objects.filter(user=staff_user, name='backup').first()
    assert backup is not None
    assert backup.token_set.count() == 10


@pytest.mark.django_db
def test_setup_2fa_command_idempotente(staff_user):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    call_command('setup_2fa', 'admin2fa@test.com')
    with pytest.raises(CommandError):
        call_command('setup_2fa', 'admin2fa@test.com')
    # com --force recria
    call_command('setup_2fa', 'admin2fa@test.com', '--force')
    assert TOTPDevice.objects.filter(user=staff_user).count() == 1


@pytest.mark.django_db
def test_setup_2fa_email_inexistente():
    from django.core.management import call_command
    from django.core.management.base import CommandError
    with pytest.raises(CommandError):
        call_command('setup_2fa', 'naoexiste@test.com')


@pytest.mark.django_db
def test_decorator_redireciona_sem_device(client, staff_user):
    """staff_otp_required deve redirecionar para setup se sem device."""
    from aranha_estetica.decorators_2fa import staff_otp_required
    from django.http import HttpResponse

    @staff_otp_required
    def view(request):
        return HttpResponse('ok')

    client.force_login(staff_user)
    request = client.get('/').wsgi_request
    request.user = staff_user
    resp = view(request)
    assert resp.status_code == 302
    assert '/account/two_factor/setup' in resp.url or 'setup' in resp.url


@pytest.mark.django_db
def test_login_publico_e_booking_nao_afetados(client):
    """Endpoints publicos nao devem exigir 2FA."""
    resp = client.get('/')
    assert resp.status_code == 200
    resp = client.get('/agendamento/')
    assert resp.status_code in (200, 302)
