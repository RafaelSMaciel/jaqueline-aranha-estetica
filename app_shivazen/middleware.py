"""Middlewares de seguranca — ShivaZen.

Inclui Content-Security-Policy com nonce por request e headers adicionais
(Permissions-Policy, X-Content-Type-Options, Cross-Origin-*).
"""
import secrets


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
    via context processor `shivazen_globals`. Em scripts/styles inline
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
            "frame-src 'self' https://www.google.com",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "base-uri 'self'",
            "object-src 'none'",
        ])
        response["Content-Security-Policy"] = csp
        return response
