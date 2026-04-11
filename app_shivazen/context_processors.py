"""Context processors globais — ShivaZen"""
import os


def shivazen_globals(request):
    """Injeta variaveis globais em todos os templates."""
    return {
        'WHATSAPP_NUMERO': os.environ.get('WHATSAPP_NUMERO', '5517000000000'),
    }
