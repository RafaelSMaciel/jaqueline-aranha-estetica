"""Middlewares de seguranca — Jaqueline Aranha Estetica.

Inclui Content-Security-Policy com nonce por request e headers adicionais
(Permissions-Policy, X-Content-Type-Options, Cross-Origin-*).
"""
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.db import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import resolve, reverse
from django.urls.exceptions import Resolver404

from .utils import dois_fatores


class Enforce2FAMiddleware:
    """Exige o desafio 2FA (TOTP) nas areas autenticadas da equipe.

    Fluxo:
      1. Usuario loga (sessao criada);
      2. Com TOTPDevice confirmado e sessao nao verificada -> challenge;
      3. ADMIN sem TOTP com 2FA obrigatorio (flag gravada no login) -> cadastro;
      4. Token correto -> sessao verificada (flag + django_otp.login) libera tudo,
         inclusive o /django-admin-sv/ (AdminSiteOTPRequired exige is_verified()).

    Em /api/ a resposta e 403 JSON em vez de redirect HTML.
    """

    EXEMPT_NAMES = {
        'admin_2fa_challenge', 'admin_2fa_verify', 'admin_2fa_setup',
        'usuario_login', 'usuario_logout',
        'password_reset', 'password_reset_done',
        'password_reset_confirm', 'password_reset_complete',
    }

    # Prefixos de URL protegidos pelo desafio 2FA. Centralizado numa constante
    # unica para evitar que uma rota administrativa nova fique sem 2FA por engano.
    # ADMIN_PREFIX deve bater com clinica/urls.py.
    ADMIN_PREFIX = '/django-admin-sv/'
    API_PREFIX = '/api/'
    PROTECTED_PREFIXES = ('/painel/', '/profissional/', API_PREFIX, ADMIN_PREFIX)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            return self.get_response(request)

        path = request.path or ''
        if not path.startswith(self.PROTECTED_PREFIXES):
            return self.get_response(request)

        try:
            match = resolve(path)
            if match.url_name in self.EXEMPT_NAMES:
                return self.get_response(request)
        except Resolver404:
            pass

        # Cadastro obrigatorio pendente (ADMIN sem TOTP): so a tela de 2FA
        if request.session.get(dois_fatores.SESSION_CADASTRO_PENDENTE):
            return self._barrar(request, 'aranha:admin_2fa_setup')

        if dois_fatores.sessao_verificada(request):
            return self.get_response(request)

        try:
            if dois_fatores.tem_2fa(request.user):
                return self._barrar(request, 'aranha:admin_2fa_challenge')
            # Django admin exige OTP verificado: staff sem TOTP cadastra antes
            # (senao o AdminSiteOTPRequired devolve p/ o login em loop).
            if path.startswith(self.ADMIN_PREFIX) and request.user.is_staff:
                return self._barrar(request, 'aranha:admin_2fa_setup')
        except (ImportError, OperationalError, ProgrammingError):
            # TOTPDevice nao disponivel (app desinstalado) ou tabela inexistente
            # (migrations pendentes). Nao bloqueia request — segue sem 2FA challenge.
            pass

        return self.get_response(request)

    def _barrar(self, request, url_name):
        if request.path.startswith(self.API_PREFIX):
            return JsonResponse({'detail': '2fa_required'}, status=403)
        destino = reverse(url_name)
        return redirect(f'{destino}?{urlencode({"next": request.get_full_path()})}')


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
    use `<script nonce="{{ csp_nonce }}">` / `<style nonce="{{ csp_nonce }}">`.

    `script-src` e `style-src`: SEM `'unsafe-inline'`. Todos os blocks
    `<script>`/`<style>` devem ter nonce. Handlers inline (onclick=...) NAO
    sao permitidos (sem `script-src-attr`, vale o script-src com nonce):
    use data-* + listener em static/src/js. So `style-src-attr` segue com
    `'unsafe-inline'` (atributos style="...").

    `frame-ancestors 'none'` em tudo, exceto respostas marcadas com
    @xframe_options_exempt (widget /embed/agendar/), que usam
    settings.EMBED_FRAME_ANCESTORS (default: qualquer origem https).

    Hosts externos: so os realmente usados (jsdelivr: FullCalendar, Chart.js,
    swagger-ui; cdnjs: embed; Google Fonts; Turnstile).
    """

    ALLOWED_SCRIPT_SRCS = [
        "'self'",
        "https://cdn.jsdelivr.net",
        "https://cdnjs.cloudflare.com",
        "https://challenges.cloudflare.com",
    ]
    ALLOWED_STYLE_SRCS = [
        "'self'",
        "https://cdn.jsdelivr.net",
        "https://cdnjs.cloudflare.com",
        "https://fonts.googleapis.com",
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
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        nonce = secrets.token_urlsafe(16)
        request.csp_nonce = nonce

        response = self.get_response(request)

        script_src = self.ALLOWED_SCRIPT_SRCS + [f"'nonce-{nonce}'"]
        style_src = self.ALLOWED_STYLE_SRCS + [f"'nonce-{nonce}'"]
        connect_src = list(self.ALLOWED_CONNECT_SRCS)

        # Em DEBUG (desenvolvimento), o Vite HMR carrega scripts e CSS de
        # http://localhost:5173 e abre um WebSocket ws://localhost:5173 para
        # hot-reload. Sem essas origens na CSP, o navegador bloquearia os
        # assets do dev server. NAO alterar CSP de producao.
        if settings.DEBUG:
            script_src = script_src + ["http://localhost:5173"]
            # Vite dev injeta <style> inline (sem nonce) para o HMR de CSS. Com
            # nonce presente os browsers ignoram 'unsafe-inline', entao em DEBUG
            # montamos style-src SEM nonce + 'unsafe-inline'. Producao fica estrita.
            style_src = self.ALLOWED_STYLE_SRCS + ["http://localhost:5173", "'unsafe-inline'"]
            connect_src = connect_src + [
                "http://localhost:5173",
                "ws://localhost:5173",
            ]

        # Widget embutivel (@xframe_options_exempt): libera o iframe externo.
        if getattr(response, 'xframe_options_exempt', False):
            frame_ancestors = getattr(settings, 'EMBED_FRAME_ANCESTORS', '') or 'https:'
        else:
            frame_ancestors = "'none'"

        csp = "; ".join([
            "default-src 'self'",
            f"script-src {' '.join(script_src)}",
            f"style-src {' '.join(style_src)}",
            "style-src-attr 'unsafe-inline'",
            f"font-src {' '.join(self.ALLOWED_FONT_SRCS)}",
            f"img-src {' '.join(self.ALLOWED_IMG_SRCS)}",
            f"connect-src {' '.join(connect_src)}",
            "frame-src 'self' https://www.google.com https://challenges.cloudflare.com",
            f"frame-ancestors {frame_ancestors}",
            "form-action 'self'",
            "base-uri 'self'",
            "object-src 'none'",
        ])
        response["Content-Security-Policy"] = csp
        return response
