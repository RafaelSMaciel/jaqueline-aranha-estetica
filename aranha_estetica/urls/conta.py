"""Conta da equipe: login, recuperacao de senha e 2FA."""
from django.urls import path

from ..views.conta import autenticacao, dois_fatores

urlpatterns = [
    # ─── Admin Login (URL oculta) ───
    path('admin-login/', autenticacao.usuario_login, name='usuario_login'),
    path('admin-logout/', autenticacao.usuario_logout, name='usuario_logout'),
    # ─── Recuperação de Senha ───
    path('admin-login/recuperar/', autenticacao.ClinicaPasswordResetView.as_view(), name='password_reset'),
    path('admin-login/recuperar/enviado/', autenticacao.ClinicaPasswordResetDoneView.as_view(), name='password_reset_done'),
    path('admin-login/recuperar/<uidb64>/<token>/', autenticacao.ClinicaPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('admin-login/recuperar/completo/', autenticacao.ClinicaPasswordResetCompleteView.as_view(), name='password_reset_complete'),
    # ─── 2FA (TOTP) ───
    path('painel/seguranca/2fa/', dois_fatores.admin_2fa_setup, name='admin_2fa_setup'),
    path('painel/seguranca/2fa/verificar/', dois_fatores.admin_2fa_verify, name='admin_2fa_verify'),
    path('painel/seguranca/2fa/challenge/', dois_fatores.admin_2fa_challenge, name='admin_2fa_challenge'),
]
