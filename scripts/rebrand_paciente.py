"""Script seguro: troca Cliente->Paciente / cliente->paciente apenas em
texto visivel (HTML templates + emails + python user-facing strings).

Skip:
- Atributos HTML (class=, id=, name=, value=, href=, action=, src=)
- Bindings Django {{ ... }} e blocos {% ... %}
- CSS dentro de <style> ... </style>
- JavaScript dentro de <script> ... </script>
- Comentarios HTML <!-- ... -->
- Comentarios Django {# ... #}
"""
from __future__ import annotations
import re
import sys
from pathlib import Path


# Substituicoes case-sensitive
PAIRS = [
    (re.compile(r'\bClientes\b'), 'Pacientes'),
    (re.compile(r'\bCliente\b'), 'Paciente'),
    (re.compile(r'\bclientes\b'), 'pacientes'),
    (re.compile(r'\bcliente\b'), 'paciente'),
]


def _replace_text(text: str) -> str:
    for pat, repl in PAIRS:
        text = pat.sub(repl, text)
    return text


def transform_html(content: str) -> tuple[str, int]:
    """Anda pelo HTML mantendo blocos seguros intactos."""
    out = []
    pos = 0
    count = 0
    # Padrao multiplexador: pega pedacos a preservar
    # IMPORTANTE: blocos {% block ..._css %}...{% endblock %} e ..._js sao
    # CSS/JS dentro de Django, nao texto visivel — preservar inteiros.
    PRESERVE = re.compile(
        r'<!--.*?-->'
        r'|<style\b[^>]*>.*?</style>'
        r'|<script\b[^>]*>.*?</script>'
        r'|\{%\s*block\s+\w*(?:css|js|style|script)\w*\s*%\}.*?\{%\s*endblock\s*\w*\s*%\}'
        r'|\{\#.*?\#\}'
        r'|\{%.*?%\}'
        r'|\{\{.*?\}\}'
        r'|<[^>]+>',  # tags HTML inteiras (atributos preservados)
        re.DOTALL | re.IGNORECASE,
    )
    for m in PRESERVE.finditer(content):
        plain = content[pos:m.start()]
        new_plain = _replace_text(plain)
        if new_plain != plain:
            count += sum(1 for p, _ in PAIRS for _ in p.finditer(plain))
        out.append(new_plain)
        out.append(m.group(0))
        pos = m.end()
    plain = content[pos:]
    new_plain = _replace_text(plain)
    if new_plain != plain:
        count += sum(1 for p, _ in PAIRS for _ in p.finditer(plain))
    out.append(new_plain)
    return ''.join(out), count


def transform_python_strings(content: str) -> tuple[str, int]:
    """Troca dentro de strings literais que pareçam mensagens user-facing.

    Heuristica: troca apenas em strings que ja contem espacos e palavras
    de saudacao/instrucao tipicas (acentos pt, exclamacao, etc.).
    Conservador: NAO troca em chaves, assertions de teste, paths, etc.
    """
    SAFE_STR = re.compile(
        r"(?P<q>['\"])"
        r"(?P<body>(?:\\.|(?!(?P=q)).){5,200})"
        r"(?P=q)"
    )
    USER_FACING = re.compile(r'[A-Z][a-z]+ [a-z]+|[a-z]+ [a-z]+ [a-z]+')
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        body = m.group('body')
        if not USER_FACING.search(body):
            return m.group(0)
        new_body = _replace_text(body)
        if new_body != body:
            count += 1
        return f"{m.group('q')}{new_body}{m.group('q')}"

    return SAFE_STR.sub(repl, content), count


SKIP_FILES = {
    'rebrand_paciente.py',
    'gerar_docx_tecnica.py',
}
SKIP_DIRS = {
    '__pycache__', 'migrations', 'tests', '.git',
    'staticfiles', 'node_modules', '.claude', 'outputs',
}


def walk_html():
    for path in Path('app_shivazen/templates').rglob('*.html'):
        yield path
    for path in Path('app_shivazen/templates').rglob('*.txt'):
        yield path


def main():
    total = 0
    files_changed = []
    # HTML/templates
    for path in walk_html():
        if any(d in path.parts for d in SKIP_DIRS):
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        new, cnt = transform_html(content)
        if cnt and new != content:
            path.write_text(new, encoding='utf-8', newline='')
            files_changed.append((str(path), cnt))
            total += cnt

    print(f'\n{len(files_changed)} arquivos alterados, ~{total} substituicoes aproximadas.')
    for f, c in files_changed[:50]:
        print(f'  {f}: ~{c}')


if __name__ == '__main__':
    main()
