"""Middlewares de seguranca — Jaqueline Aranha Estetica.

Inclui Content-Security-Policy com nonce por request e headers adicionais
(Permissions-Policy, X-Content-Type-Options, Cross-Origin-*) e o contexto do
banco (autor/IP/origem) lido pela trilha historico_alteracao.
"""
import logging
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.db import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import resolve, reverse
from django.urls.exceptions import Resolver404
from django.utils.cache import add_never_cache_headers

from .utils import dois_fatores
from .ator import Ator, atuando

logger = logging.getLogger(__name__)


class Enforce2FAMiddleware:
    """Exige o desafio 2FA (TOTP) nas areas autenticadas da equipe.

    Fluxo:
      1. Usuario loga (sessao criada);
      2. Com TOTPDevice confirmado e sessao nao verificada -> challenge;
      3. Sem TOTP com 2FA obrigatorio (ADMIN; PROFISSIONAL se ligado) -> cadastro.
         Conferido a cada request, nao so pela flag do login: sessao promovida
         a ADMIN depois do login, anterior ao deploy ou aberta com a valvula
         ADMIN_2FA_OBRIGATORIO desligada tambem cai no cadastro;
      4. Token correto -> sessao verificada (flag + django_otp.login) libera tudo,
         inclusive o /django-admin-sv/ (AdminSiteOTPRequired exige is_verified()).

    Em /api/ e /webpush/ a resposta e 403 JSON em vez de redirect HTML.
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
    # Assinar push = passar a receber nome de cliente/procedimento/horario
    WEBPUSH_PREFIXES = ('/webpush/subscribe/', '/webpush/unsubscribe/')
    PROTECTED_PREFIXES = ('/painel/', '/profissional/', API_PREFIX, ADMIN_PREFIX, *WEBPUSH_PREFIXES)
    JSON_PREFIXES = (API_PREFIX, *WEBPUSH_PREFIXES)

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
            # 2FA obrigatorio conferido a CADA request (nao so pela flag do
            # login): sessao promovida a ADMIN depois do login, anterior ao
            # deploy ou aberta com a valvula desligada tambem cai no cadastro.
            if (dois_fatores.obrigatorio_para(request.user)
                    and dois_fatores.sessao_do_login_equipe(request)):
                return self._barrar(request, 'aranha:admin_2fa_setup')
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
        if request.path.startswith(self.JSON_PREFIXES):
            return JsonResponse({'detail': '2fa_required'}, status=403)
        destino = reverse(url_name)
        return redirect(f'{destino}?{urlencode({"next": request.get_full_path()})}')


class SecurityHeadersMiddleware:
    """Headers de seguranca adicionais nao cobertos pelo Django core.

    Areas privadas (equipe, portal da cliente, LGPD, API) saem sem cache no
    navegador (no-store): no computador compartilhado da recepcao, "voltar"
    depois do logout nao reexibe dado de saude/pessoal do bfcache ou do cache
    de disco (LGPD art. 11/46). Defesa em profundidade p/ view nova que
    esquecer o @never_cache; Cache-Control definido pela view e respeitado.
    Rotas de link magico (PREFIXOS_TOKEN) idem: a pagina do token mostra
    nome, horario e ficha de saude da cliente.

    Privada ou com token: X-Robots-Tag noindex (o robots.txt so pede p/ nao buscar; um link
    vazado ainda podia entrar no indice so com a URL — e a URL carrega o token).
    """

    PERMISSIONS_POLICY = (
        "geolocation=(self), camera=(), microphone=(), payment=(), "
        "usb=(), magnetometer=(), gyroscope=(), accelerometer=(), "
        "autoplay=(self), fullscreen=(self)"
    )
    # Bater com aranha_estetica/urls/ e clinica/urls.py
    PREFIXOS_PRIVADOS = (
        '/painel/', '/profissional/', '/meus-agendamentos/', '/lgpd/',
        Enforce2FAMiddleware.ADMIN_PREFIX, Enforce2FAMiddleware.API_PREFIX,
    )

    # Rotas cujo path carrega token de link magico: sem Referer, senao o token
    # vaza p/ CDN/links externos abertos a partir da pagina; e sem cache
    # (no-store), como as areas privadas.
    PREFIXOS_TOKEN = (
        '/confirmar/', '/nps/', '/anamnese/', '/pesquisa/',
        '/termo/', '/lgpd/unsubscribe/', '/admin-login/recuperar/', '/agenda/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path or ''
        com_token = path.startswith(self.PREFIXOS_TOKEN)
        privada = com_token or path.startswith(self.PREFIXOS_PRIVADOS)
        if privada and not response.has_header('Cache-Control'):
            add_never_cache_headers(response)
        if privada or getattr(settings, 'AMBIENTE_TESTE', False):
            # ambiente de testes publicado: nada entra no indice (concorreria com o dominio real)
            response.setdefault('X-Robots-Tag', 'noindex, nofollow, noarchive')
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Permissions-Policy", self.PERMISSIONS_POLICY)
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if com_token:
            response["Referrer-Policy"] = "no-referrer"
        else:
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

    Hosts externos: so os realmente usados (Google Fonts; Turnstile). O
    jsdelivr serve QUALQUER pacote npm/GitHub — liberar o host inteiro deixaria
    um <script src> injetado rodar apesar do nonce. Por isso so os caminhos
    das libs em uso (JSDELIVR_PATHS).
    Trocou a versao da lib no template? Atualize aqui (test_csp confere
    templates x allowlist).
    """

    JSDELIVR_PATHS = [
        "https://cdn.jsdelivr.net/npm/fullcalendar@6.1.11/",           # painel/calendar
        "https://cdn.jsdelivr.net/npm/@fullcalendar/core@6.1.11/",     # painel/calendar
        "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/",                # painel/overview
    ]
    ALLOWED_SCRIPT_SRCS = [
        "'self'",
        *JSDELIVR_PATHS,
        "https://challenges.cloudflare.com",
    ]
    ALLOWED_STYLE_SRCS = [
        "'self'",
        *JSDELIVR_PATHS,
    ]
    ALLOWED_FONT_SRCS = [
        "'self'",
        "data:",
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
        font_src = list(self.ALLOWED_FONT_SRCS)

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
            font_src = font_src + ["http://localhost:5173"]   # fontes self-hosted no dev server

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
            f"font-src {' '.join(font_src)}",
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


class ContextoBancoMiddleware:
    """Abre o Ator do request (aranha_estetica/ator.py): autor/IP/origem='web' p/ as duas
    trilhas — LogAuditoria (registrar_log) e historico_alteracao (spec padrao-sql §5.3).

    No Postgres o Ator vai as GUCs ``app.usuario_id`` / ``app.ip_origem`` / ``app.origem`` no
    inicio e e LIMPO no fim, que ``fn_registrar_historico`` grava em ``historico_alteracao``
    (no SQLite so o ContextVar). Vai logo depois do ``OTPMiddleware`` (precisa de request.user).

    Nivel de SESSAO (``set_config(..., false)``): o Django roda em autocommit e
    ``SET LOCAL`` morreria no fim do proprio statement. Como CONN_MAX_AGE
    reaproveita a conexao no proximo request da mesma thread, o contexto e SEMPRE
    definido no inicio ('' p/ anonimo) e limpo no fim; se definir ou limpar falhar,
    a conexao e descartada (nunca volta com o usuario de outra pessoa). Fica fora
    de transaction.atomic. Resposta em streaming roda depois do ``finally`` (nenhuma
    escreve hoje). PgBouncer em modo transacao quebraria GUC de sessao: nesse dia,
    trocar p/ ATOMIC_REQUESTS + SET LOCAL. Custo: 2 round-trips por request dinamico
    (estaticos saem antes, no WhiteNoise); o liveness (/healthz/) nao toca o banco.
    """

    CAMINHOS_SEM_BANCO = ('/healthz/',)  # liveness do Railway: nao pode depender do Postgres
    ORIGEM = 'web'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in self.CAMINHOS_SEM_BANCO:
            return self.get_response(request)
        with atuando(Ator.do_request(request, self.ORIGEM)):
            return self.get_response(request)
