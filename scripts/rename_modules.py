"""Rename Python imports + URL namespace + project paths em todo codigo.

Replaces (ordem importa):
  'aranha_estetica.signals'    -> 'aranha_estetica.signals'
  'from aranha_estetica'       -> 'from aranha_estetica'
  'aranha_estetica.X'          -> 'aranha_estetica.X'  (genérico)
  'clinica.settings'       -> 'clinica.settings'
  'clinica.urls'           -> 'clinica.urls'
  'clinica.wsgi'           -> 'clinica.wsgi'
  'clinica.asgi'           -> 'clinica.asgi'
  'clinica.celery'         -> 'clinica.celery'
  '-A shivazen'             -> '-A clinica'   (Procfile celery)
  'gunicorn shivazen.'      -> 'gunicorn clinica.'
  "Celery('clinica')"      -> "Celery('clinica')"
  "{% url 'aranha:"       -> "{% url 'aranha:"
  "{% url \"aranha:"      -> "{% url \"aranha:"
  'reverse("aranha:'      -> 'reverse("aranha:'
  "reverse('aranha:"      -> "reverse('aranha:"
  "reverse_lazy('aranha:" -> "reverse_lazy('aranha:"
  'redirect("aranha:'     -> 'redirect("aranha:'
  "redirect('aranha:"     -> "redirect('aranha:"
  'aranha:'  (URL string) -> 'aranha:'  (resolver_match)
  app_name = 'aranha'     -> app_name = 'aranha'
  namespace='aranha'      -> namespace='aranha'
  resolver_match.url_name in 'shivazen,..' -> tag list, mantém nome do URL
  resolver_match... shivazen?  -> nada (resolver_match.namespace == 'aranha' se mexer)

PRESERVA: 'shivazen-app' (repo dir name local)
"""
import os
import re

# Imports + module path replacements (Python files)
PY_REPLACEMENTS = [
    # Imports estilo `from X import Y`
    ('from aranha_estetica', 'from aranha_estetica'),
    # `import aranha_estetica.foo`
    ('import aranha_estetica.', 'import aranha_estetica.'),
    # `import aranha_estetica` exato
    (re.compile(r'\bimport app_shivazen\b(?!\.)'), 'import aranha_estetica'),
    # `aranha_estetica.X` em strings (e.g. AUTH_USER_MODEL)
    ('aranha_estetica.', 'aranha_estetica.'),
    # 'aranha_estetica' (string literal)
    ("'aranha_estetica'", "'aranha_estetica'"),
    ('"aranha_estetica"', '"aranha_estetica"'),

    # Project module
    ('clinica.settings', 'clinica.settings'),
    ('clinica.urls', 'clinica.urls'),
    ('clinica.wsgi', 'clinica.wsgi'),
    ('clinica.asgi', 'clinica.asgi'),
    ('clinica.celery', 'clinica.celery'),
    ("Celery('clinica')", "Celery('clinica')"),
    ('Celery("clinica")', 'Celery("clinica")'),

    # URL namespace
    ("'aranha:", "'aranha:"),
    ('"aranha:', '"aranha:'),
    ("app_name = 'aranha'", "app_name = 'aranha'"),
    ('app_name = "aranha"', 'app_name = "aranha"'),
    ("namespace='aranha'", "namespace='aranha'"),
    ('namespace="aranha"', 'namespace="aranha"'),
]

# Templates: URL namespace
TPL_REPLACEMENTS = [
    ("{% url 'aranha:", "{% url 'aranha:"),
    ('{% url "aranha:', '{% url "aranha:'),
    ("'shivazen,", "'aranha,"),  # resolver_match.url_name in 'shivazen,...'
    ("'shivazen'", "'aranha'"),
]

# Infra files (manage.py, wsgi, Procfile, railway.json, etc.)
INFRA_REPLACEMENTS = [
    ('clinica.settings', 'clinica.settings'),
    ('clinica.urls', 'clinica.urls'),
    ('clinica.wsgi', 'clinica.wsgi'),
    ('clinica.asgi', 'clinica.asgi'),
    ('clinica.celery', 'clinica.celery'),
    ('shivazen project', 'clinica project'),
    ('-A shivazen ', '-A clinica '),
    (' shivazen.', ' clinica.'),
    ('Celery shivazen', 'Celery clinica'),
]


def apply_substitutions(text: str, repls):
    for pattern, replacement in repls:
        if hasattr(pattern, 'sub'):
            text = pattern.sub(replacement, text)
        else:
            text = text.replace(pattern, replacement)
    return text


def process_file(fp: str, repls):
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        return False
    new_content = apply_substitutions(content, repls)
    if new_content != content:
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    return False


def main():
    targets_py = []
    targets_tpl = []
    targets_infra = []

    skip_dirs = {'.git', 'node_modules', '__pycache__', '.claude', 'venv', '.venv', 'tmp_req'}

    # Code dirs
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            fp = os.path.join(root, f)
            if f.endswith('.py'):
                targets_py.append(fp)
            elif f.endswith('.html'):
                targets_tpl.append(fp)

    # Infra files (root)
    for f in ['Procfile', 'railway.json', 'manage.py', 'Dockerfile', 'docker-compose.yml', '.env.example']:
        if os.path.isfile(f):
            targets_infra.append(f)

    print(f'Python: {len(targets_py)}, Templates: {len(targets_tpl)}, Infra: {len(targets_infra)}')

    py_changed = 0
    for fp in targets_py:
        if process_file(fp, PY_REPLACEMENTS):
            py_changed += 1
    print(f'  Python alterados: {py_changed}')

    tpl_changed = 0
    for fp in targets_tpl:
        if process_file(fp, TPL_REPLACEMENTS):
            tpl_changed += 1
    print(f'  Templates alterados: {tpl_changed}')

    infra_changed = 0
    for fp in targets_infra:
        if process_file(fp, INFRA_REPLACEMENTS):
            infra_changed += 1
            print(f'    infra: {fp}')

    print(f'TOTAL: {py_changed + tpl_changed + infra_changed} arquivos')


if __name__ == '__main__':
    main()
