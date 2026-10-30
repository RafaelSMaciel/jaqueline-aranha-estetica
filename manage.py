#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

# --- ADICIONE ESSAS 3 LINHAS ---
from dotenv import load_dotenv
if os.path.exists('.env'):
    load_dotenv('.env')
# --- FIM DAS LINHAS ADICIONADAS ---


def _saida_utf8():
    """Windows: o console (cp1252) recusava '✓'/'—' dos templates e o backend de e-mail de console
    do dev falhava em TODO e-mail (UnicodeEncodeError); a saida dos comandos saia com acento
    quebrado. Linux/Docker ja sao UTF-8."""
    if sys.platform != 'win32':
        return
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding='utf-8', errors='backslashreplace')
        except (AttributeError, ValueError):
            pass


def main():
    """Run administrative tasks."""
    _saida_utf8()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clinica.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
