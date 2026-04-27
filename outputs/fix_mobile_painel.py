"""Retry painel mobile screenshots — bypass debug toolbar via form.submit()."""
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8000'
EMAIL = 'screenshot@local.test'
PASSWORD = 'ScreenShot123!@#'

PAINEL_PAGES = [
    ('13_painel', '/painel/'),
    ('14_painel_clientes', '/painel/clientes/'),
    ('15_painel_agendamentos', '/painel/agendamentos/'),
    ('16_painel_procedimentos', '/painel/procedimentos/'),
    ('17_painel_profissionais', '/painel/profissionais/'),
]


def main():
    out_dir = Path('outputs/screenshots/mobile')
    log = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={'width': 390, 'height': 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                       'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 '
                       'Mobile/15E148 Safari/604.1',
        )
        page = ctx.new_page()
        page.goto(f'{BASE}/admin-login/', wait_until='networkidle')
        page.fill('input[name="username"]', EMAIL)
        page.fill('input[name="password"]', PASSWORD)
        try:
            page.click('button[type="submit"]', timeout=8000)
        except Exception as exc:
            log.append(f'click_fail mobile -> {exc}')
            page.evaluate("document.querySelector('form').submit()")
        page.wait_for_load_state('networkidle', timeout=15000)
        log.append(f'LOGIN mobile final={page.url}')
        page.close()

        for name, url in PAINEL_PAGES:
            page = ctx.new_page()
            try:
                resp = page.goto(f'{BASE}{url}', wait_until='networkidle', timeout=15000)
                status = resp.status if resp else 0
                final = page.url
                # delete redirect_login files first
                for stale in out_dir.glob(f'{name}*'):
                    try:
                        stale.unlink()
                    except Exception:
                        pass
                suffix = ''
                if status >= 500:
                    suffix = '_5xx'
                elif status == 404:
                    suffix = '_404'
                elif '/admin-login/' in final:
                    suffix = '_redirect_login'
                fname = f'{name}{suffix}.png'
                page.screenshot(path=str(out_dir / fname), full_page=True, timeout=15000)
                log.append(f'OK {name} -> {fname} status={status}')
            except Exception as exc:
                log.append(f'FAIL {name} -> {exc}')
            page.close()
        ctx.close()
        browser.close()
    print('\n'.join(log))


if __name__ == '__main__':
    main()
