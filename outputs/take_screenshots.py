"""Screenshot full-page Desktop + Mobile via Playwright (Chromium)."""
from __future__ import annotations
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright


BASE = 'http://127.0.0.1:8000'
EMAIL = 'screenshot@local.test'
PASSWORD = 'ScreenShot123!@#'

PAGES = [
    # public
    ('01_home', '/'),
    ('02_quem_somos', '/quem-somos/'),
    ('03_equipe', '/equipe/'),
    ('04_servicos_faciais', '/servicos/faciais/'),
    ('05_agendamento_publico', '/agendamento/'),
    ('06_lista_espera', '/lista-espera/'),
    ('07_promocoes', '/promocoes/'),
    ('08_contato', '/contato/'),
    ('09_politica_privacidade', '/politica-de-privacidade/'),
    ('10_termos_uso', '/termos-de-uso/'),
    # auth pages (no login)
    ('11_login_2fa', '/account/login/'),
    ('12_admin_login_legacy', '/admin-login/'),
]

PAINEL_PAGES = [
    ('13_painel', '/painel/'),
    ('14_painel_clientes', '/painel/clientes/'),
    ('15_painel_agendamentos', '/painel/agendamentos/'),
    ('16_painel_procedimentos', '/painel/procedimentos/'),
    ('17_painel_profissionais', '/painel/profissionais/'),
]


def login(page):
    """Login pelo flow legado /admin-login/ (POST email+senha)."""
    page.goto(f'{BASE}/admin-login/', wait_until='networkidle')
    page.fill('input[name="username"]', EMAIL)
    page.fill('input[name="password"]', PASSWORD)
    with page.expect_navigation():
        page.click('button[type="submit"]')


def shoot(context, name, url, out_dir, log):
    page = context.new_page()
    full_url = f'{BASE}{url}' if url.startswith('/') else url
    try:
        resp = page.goto(full_url, wait_until='networkidle', timeout=15000)
    except Exception as exc:
        log.append(f'TIMEOUT {name} {url} -> {exc}')
        page.close()
        return
    status = resp.status if resp else 0
    final = page.url
    suffix = ''
    if status >= 500:
        suffix = '_5xx'
        log.append(f'5xx {name} status={status} final={final}')
    elif status == 404:
        suffix = '_404'
        log.append(f'404 {name} status={status}')
    elif final.rstrip('/') != full_url.rstrip('/') and '/admin-login/' in final:
        suffix = '_redirect_login'
    elif final.rstrip('/') != full_url.rstrip('/'):
        suffix = '_redirect'
    fname = f'{name}{suffix}.png'
    out = out_dir / fname
    try:
        page.screenshot(path=str(out), full_page=True, timeout=15000)
        log.append(f'OK {name} -> {out}')
    except Exception as exc:
        log.append(f'SCREENSHOT_FAIL {name} -> {exc}')
    page.close()


def main():
    out_root = Path('outputs/screenshots')
    desktop_dir = out_root / 'desktop'
    mobile_dir = out_root / 'mobile'
    desktop_dir.mkdir(parents=True, exist_ok=True)
    mobile_dir.mkdir(parents=True, exist_ok=True)

    log = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ── DESKTOP ────────────────────────────────────────────
        ctx_desktop = browser.new_context(viewport={'width': 1366, 'height': 768})
        for name, url in PAGES:
            shoot(ctx_desktop, name, url, desktop_dir, log)
        # Painel desktop (com login)
        page = ctx_desktop.new_page()
        try:
            page.goto(f'{BASE}/admin-login/', wait_until='networkidle')
            page.fill('input[name="username"]', EMAIL)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state('networkidle', timeout=10000)
            log.append(f'LOGIN desktop final={page.url}')
        except Exception as exc:
            log.append(f'LOGIN_FAIL desktop -> {exc}')
        page.close()
        for name, url in PAINEL_PAGES:
            shoot(ctx_desktop, name, url, desktop_dir, log)
        ctx_desktop.close()

        # ── MOBILE (iPhone-ish) ────────────────────────────────
        ctx_mobile = browser.new_context(
            viewport={'width': 390, 'height': 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                       'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 '
                       'Mobile/15E148 Safari/604.1',
        )
        for name, url in PAGES:
            shoot(ctx_mobile, name, url, mobile_dir, log)
        page = ctx_mobile.new_page()
        try:
            page.goto(f'{BASE}/admin-login/', wait_until='networkidle')
            page.fill('input[name="username"]', EMAIL)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state('networkidle', timeout=10000)
            log.append(f'LOGIN mobile final={page.url}')
        except Exception as exc:
            log.append(f'LOGIN_FAIL mobile -> {exc}')
        page.close()
        for name, url in PAINEL_PAGES:
            shoot(ctx_mobile, name, url, mobile_dir, log)
        ctx_mobile.close()

        browser.close()

    print('\n'.join(log))


if __name__ == '__main__':
    main()
