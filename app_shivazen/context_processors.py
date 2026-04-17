"""Context processors globais — Plataforma de Clinicas"""
import os


def shivazen_globals(request):
    """Injeta variaveis de marca e configuracao em todos os templates."""
    return {
        'CLINIC_NAME': os.environ.get('CLINIC_NAME', 'Shiva Zen'),
        'CLINIC_SUBTITLE': os.environ.get('CLINIC_SUBTITLE', 'Clínica de Estética e Bem-Estar'),
        'CLINIC_EMAIL': os.environ.get('CLINIC_EMAIL', 'contato@clinica.com.br'),
        'CLINIC_PHONE': os.environ.get('CLINIC_PHONE', '(17) 99999-0000'),
        'CLINIC_ADDRESS': os.environ.get('CLINIC_ADDRESS', 'R. Humberto Delboni, 1107 - Jardim Fuscaldo, São José do Rio Preto - SP'),
        'WHATSAPP_NUMERO': os.environ.get('WHATSAPP_NUMERO', '5517999990000'),
        'SITE_URL': os.environ.get('SITE_URL', 'http://127.0.0.1:8000'),
        'INSTAGRAM_URL': os.environ.get('INSTAGRAM_URL', 'https://www.instagram.com/shivazensjrp/'),
    }
