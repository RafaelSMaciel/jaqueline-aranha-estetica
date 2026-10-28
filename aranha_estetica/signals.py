"""Signals de model — so o que nao e regra de negocio.

Os efeitos do atendimento (faltas, pacote, lista de espera, push/gcal, comissao, cashback,
retorno) sao chamados explicitamente por services.atendimento (FSM e services.agenda), nao por
post_save.
"""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Configuracao


@receiver([post_save, post_delete], sender=Configuracao)
def invalidar_cache_branding(sender, instance, **kwargs):
    """Zera o cache de marca/contatos (utils/branding) quando Configuracao muda."""
    from .utils.branding import invalidar_cache
    invalidar_cache()
