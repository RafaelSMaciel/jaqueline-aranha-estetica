"""Screenshot v2 — escreve em outputs/screenshots/v2/{desktop,mobile}."""
from __future__ import annotations
from pathlib import Path
from playwright.sync_api import sync_playwright


BASE = 'http://127.0.0.1:8000'
EMAIL = 'screenshot@local.test'
PASSWORD = 'ScreenShot123!@#'

PAGES = [
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


def shoot(ctx, name, url, out_dir, log):
    page = ctx.new_page()
    full = f'{BASE}{url}'
    try:
        resp = page.goto(full, wait_until='networkidle', timeout=15000)
        status = resp.status if resp else 0
        final = page.url
        suffix = ''
        if status >= 500:
            suffix = '_5xx'
            log.append(f'5xx {name} status={status}')
        elif status == 404:
            suffix = '_404'
        elif final.rstrip('/') != full.rstrip('/') and '/admin-login/' in final:
            suffix = '_redirect_login'
        elif final.rstrip('/') != full.rstrip('/'):
            suffix = '_redirect'
        page.screenshot(path=str(out_dir / f'{name}{suffix}.png'), full_page=True, timeout=15000)
        log.append(f'OK {name} status={status}')
    except Exception as exc:
        log.append(f'FAIL {name} -> {exc}')
    page.close()


def login(ctx, log, label):
    page = ctx.new_page()
    try:
        page.goto(f'{BASE}/admin-login/', wait_until='networkidle')
        page.fill('input[name="username"]', EMAIL)
        page.fill('input[name="password"]', PASSWORD)
        try:
            page.click('button[type="submit"]', timeout=8000)
        except Exception:
            page.evaluate("document.querySelector('form').submit()")
        page.wait_for_load_state('networkidle', timeout=15000)
        log.append(f'LOGIN {label} final={page.url}')
    except Exception as exc:
        log.append(f'LOGIN_FAIL {label} -> {exc}')
    page.close()


def main():
    out_root = Path('outputs/screenshots/v2')
    desktop = out_root / 'desktop'
    mobile = out_root / 'mobile'
    desktop.mkdir(parents=True, exist_ok=True)
    mobile.mkdir(parents=True, exist_ok=True)
    log = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        ctx = browser.new_context(viewport={'width': 1366, 'height': 768})
        for n, u in PAGES:
            shoot(ctx, n, u, desktop, log)
        login(ctx, log, 'desktop')
        for n, u in PAINEL_PAGES:
            shoot(ctx, n, u, desktop, log)
        ctx.close()

        ctx = browser.new_context(
            viewport={'width': 390, 'height': 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                       'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 '
                       'Mobile/15E148 Safari/604.1',
        )
        for n, u in PAGES:
            shoot(ctx, n, u, mobile, log)
        login(ctx, log, 'mobile')
        for n, u in PAINEL_PAGES:
            shoot(ctx, n, u, mobile, log)
        ctx.close()

        browser.close()

    print('\n'.join(log))


if __name__ == '__main__':
    main()
