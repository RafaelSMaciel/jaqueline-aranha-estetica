"""Middlewares de seguranca — ShivaZen.

Inclui Content-Security-Policy com nonce por request e headers adicionais
(Permissions-Policy, X-Content-Type-Options, Cross-Origin-*).
"""
import secrets

from django.shortcuts import redirect
from django.urls import resolve, reverse
from django.urls.exceptions import Resolver404


class Enforce2FAMiddleware:
    """Exige verificacao 2FA pos-login para acessar /painel/ quando usuario tem TOTP ativo.

    Fluxo:
      1. Usuario loga (session cria);
      2. Se tiver TOTPDevice.confirmed=True e sessao sem 'otp_verified', redireciona p/ challenge;
      3. Apos verificar token correto, session['otp_verified']=True libera /painel/.
    """

    EXEMPT_NAMES = {
        'admin_2fa_challenge', 'admin_2fa_verify', 'admin_2fa_setup',
        'usuario_login', 'usuario_logout',
        'password_reset', 'password_reset_done',
        'password_reset_confirm', 'password_reset_complete',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            return self.get_response(request)

        path = request.path or ''
        if not path.startswith('/painel/') and not path.startswith('/profissional/'):
            return self.get_response(request)

        try:
            match = resolve(path)
            if match.url_name in self.EXEMPT_NAMES:
                return self.get_response(request)
        except Resolver404:
            pass

        if request.session.get('otp_verified'):
            return self.get_response(request)

        try:
            from django_otp.plugins.otp_totp.models import TOTPDevice
            if TOTPDevice.objects.filter(user=request.user, confirmed=True).exists():
                challenge_url = reverse('aranha:admin_2fa_challenge')
                return redirect(f'{challenge_url}?next={path}')
        except Exception:
            pass

        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Headers de seguranca adicionais nao cobertos pelo Django core."""

    PERMISSIONS_POLICY = (
        "geolocation=(self), camera=(), microphone=(), payment=(), "
        "usb=(), magnetometer=(), gyroscope=(), accelerometer=(), "
        "autoplay=(self), fullscreen=(self)"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Permissions-Policy", self.PERMISSIONS_POLICY)
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response


class ContentSecurityPolicyMiddleware:
    """CSP com nonce por request.

    O nonce e injetado em `request.csp_nonce` e disponivel no template
    via context processor `clinica_globals`. Em scripts/styles inline
    use `<script nonce="{{ csp_nonce }}">`.

    NOTA: `'unsafe-inline'` ainda e tolerado para compatibilidade com
    templates legados que usam handlers inline (onclick, etc). Navegadores
    modernos ignoram `unsafe-inline` quando nonce/hash estao presentes.
    """

    ALLOWED_SCRIPT_SRCS = [
        "'self'",
        "'unsafe-inline'",  # TODO: migrar templates legados e remover
        "https://cdn.jsdelivr.net",
        "https://cdnjs.cloudflare.com",
        "https://code.jquery.com",
        "https://unpkg.com",
        "https://challenges.cloudflare.com",
    ]
    ALLOWED_STYLE_SRCS = [
        "'self'",
        "'unsafe-inline'",
        "https://cdn.jsdelivr.net",
        "https://cdnjs.cloudflare.com",
        "https://fonts.googleapis.com",
        "https://unpkg.com",
    ]
    ALLOWED_FONT_SRCS = [
        "'self'",
        "data:",
        "https://fonts.gstatic.com",
        "https://cdn.jsdelivr.net",
        "https://cdnjs.cloudflare.com",
    ]
    ALLOWED_IMG_SRCS = [
        "'self'",
        "data:",
        "https:",
    ]
    ALLOWED_CONNECT_SRCS = [
        "'self'",
        "https://www.google-analytics.com",
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        nonce = secrets.token_urlsafe(16)
        request.csp_nonce = nonce

        response = self.get_response(request)

        script_src = self.ALLOWED_SCRIPT_SRCS + [f"'nonce-{nonce}'"]
        style_src = self.ALLOWED_STYLE_SRCS + [f"'nonce-{nonce}'"]

        csp = "; ".join([
            "default-src 'self'",
            f"script-src {' '.join(script_src)}",
            # CSP3: inline event handlers (onclick=...) e style="..." sao
            # governados por -attr. Permitimos inline attrs enquanto nao
            # migramos tudo pro CSS/listener externo.
            "script-src-attr 'unsafe-inline'",
            f"style-src {' '.join(style_src)}",
            "style-src-attr 'unsafe-inline'",
            f"font-src {' '.join(self.ALLOWED_FONT_SRCS)}",
            f"img-src {' '.join(self.ALLOWED_IMG_SRCS)}",
            f"connect-src {' '.join(self.ALLOWED_CONNECT_SRCS)}",
            "frame-src 'self' https://www.google.com https://challenges.cloudflare.com",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "base-uri 'self'",
            "object-src 'none'",
        ])
        response["Content-Security-Policy"] = csp
        return response
