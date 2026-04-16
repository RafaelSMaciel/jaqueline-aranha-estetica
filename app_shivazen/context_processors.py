"""Context processors globais — Plataforma de Clinicas"""
import os


def shivazen_globals(request):
    """Injeta variaveis de marca e configuracao em todos os templates."""
    return {
        'CLINIC_NAME': os.environ.get('CLINIC_NAME', 'Clinica Estetica'),
        'CLINIC_SUBTITLE': os.environ.get('CLINIC_SUBTITLE', 'Clinica de Estetica Avancada'),
        'CLINIC_EMAIL': os.environ.get('CLINIC_EMAIL', 'contato@clinica.com.br'),
        'CLINIC_PHONE': os.environ.get('CLINIC_PHONE', ''),
        'CLINIC_ADDRESS': os.environ.get('CLINIC_ADDRESS', ''),
        'WHATSAPP_NUMERO': os.environ.get('WHATSAPP_NUMERO', ''),
        'SITE_URL': os.environ.get('SITE_URL', 'http://127.0.0.1:8000'),
    }
