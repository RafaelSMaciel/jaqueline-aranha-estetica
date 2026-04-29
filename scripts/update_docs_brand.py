"""Atualiza branding em todos docs (.md, .txt) p/ 'Jaqueline Aranha Estetica'.

Replaces (case-sensitive, ordem importa):
  'Plataforma Dra. Jaqueline Aranha' -> 'Jaqueline Aranha Estetica'
  'Plataforma ShivaZen'              -> 'Jaqueline Aranha Estetica'
  'Plataforma Shiva Zen'             -> 'Jaqueline Aranha Estetica'
  'Shiva Zen'                        -> 'Jaqueline Aranha Estetica'
  'projeto Shiva Zen'                -> 'projeto Jaqueline Aranha Estetica'
  'Dra. Jaqueline Aranha'            -> 'Jaqueline Aranha'

PRESERVA (nao mexer):
  - 'shivazen-app'  (nome repo)
  - 'aranha_estetica'  (Python module)
  - 'clinica.settings'  (Python module)
  - 'aranha:'  (Django URL namespace)
  - 'shivazensjrp'  (Instagram handle)
"""
import os
import re

# Ordem importa: phrases mais longas primeiro
replacements = [
    ('Plataforma Dra. Jaqueline Aranha', 'Jaqueline Aranha Estética'),
    ('Plataforma ShivaZen', 'Jaqueline Aranha Estética'),
    ('Plataforma Shiva Zen', 'Jaqueline Aranha Estética'),
    ('projeto Shiva Zen', 'projeto Jaqueline Aranha Estética'),
    ('Shiva Zen', 'Jaqueline Aranha Estética'),
    ('SHIVA ZEN', 'JAQUELINE ARANHA ESTÉTICA'),
    ('Dra. Jaqueline Aranha', 'Jaqueline Aranha'),
]


def is_protected_context(text: str, idx: int, length: int) -> bool:
    """Skip se ocorrencia esta em contexto tecnico (URL, code path, namespace)."""
    pre = text[max(0, idx - 20):idx]
    post = text[idx + length:idx + length + 30]
    # path/url/namespace
    if 'shivazen-app' in pre + text[idx:idx + length] + post:
        return True
    return False


def apply_replacements(text: str) -> str:
    for old, new in replacements:
        # Skip se 'shivazen-app' ou 'shivazensjrp' adjacente
        text = text.replace(old, new)
    return text


def process_file(fp: str) -> bool:
    with open(fp, 'r', encoding='utf-8') as f:
        content = f.read()
    new_content = apply_replacements(content)
    if new_content != content:
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    return False


def main():
    targets = []
    # Docs root + outputs
    for root in ('docs', 'outputs'):
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for f in files:
                if f.endswith(('.md', '.txt')):
                    targets.append(os.path.join(dirpath, f))
    # README.md
    if os.path.isfile('README.md'):
        targets.append('README.md')

    print(f'docs alvo: {len(targets)}')
    changed = 0
    for fp in targets:
        if process_file(fp):
            changed += 1
            print(f'  updated: {fp}')
    print(f'TOTAL: {changed} arquivo(s) atualizado(s)')


if __name__ == '__main__':
    main()
