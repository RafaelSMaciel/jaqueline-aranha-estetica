"""Middlewares de segurança — ShivaZen"""


class ContentSecurityPolicyMiddleware:
    """Adiciona Content-Security-Policy header a todas as respostas."""

    # CDNs usados nos templates
    ALLOWED_SCRIPT_SRCS = [
        "'self'",
        "'unsafe-inline'",
        "https://cdn.jsdelivr.net",
        "https://cdnjs.cloudflare.com",
        "https://code.jquery.com",
        "https://unpkg.com",
    ]
    ALLOWED_STYLE_SRCS = [
        "'self'",
        "'unsafe-inline'",  # Bootstrap e estilos inline nos templates
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

    def __init__(self, get_response):
        self.get_response = get_response
        self.csp = "; ".join([
            f"default-src 'self'",
            f"script-src {' '.join(self.ALLOWED_SCRIPT_SRCS)}",
            f"style-src {' '.join(self.ALLOWED_STYLE_SRCS)}",
            f"font-src {' '.join(self.ALLOWED_FONT_SRCS)}",
            f"img-src {' '.join(self.ALLOWED_IMG_SRCS)}",
            f"frame-src 'self' https://www.google.com",
            f"connect-src 'self'",
            f"frame-ancestors 'none'",
            f"form-action 'self'",
            f"base-uri 'self'",
        ])

    def __call__(self, request):
        response = self.get_response(request)
        response['Content-Security-Policy'] = self.csp
        return response
