"""Adiciona nonce CSP em todos inline <style> e <script> sem atributos."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent / 'app_shivazen' / 'templates'
NONCE = '{{ csp_nonce }}'

# Match exact bare tags (no attrs) but NOT already-nonce'd ones
RE_STYLE = re.compile(r'<style>')
RE_SCRIPT = re.compile(r'<script>')

changed = []
for path in ROOT.rglob('*.html'):
    txt = path.read_text(encoding='utf-8')
    new = txt
    new = RE_STYLE.sub(f'<style nonce="{NONCE}">', new)
    new = RE_SCRIPT.sub(f'<script nonce="{NONCE}">', new)
    if new != txt:
        path.write_text(new, encoding='utf-8')
        changed.append(str(path.relative_to(ROOT)))

print(f'Arquivos alterados: {len(changed)}')
for p in changed:
    print(f'  {p}')
