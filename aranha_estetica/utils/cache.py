"""Decorator de cache + helpers de invalidacao.

Padroniza chaves, TTL e invalidacao explicita em todo o projeto.
"""
from functools import wraps

from django.core.cache import cache

# Sentinel para distinguir "ausente no cache" de "None legitimamente cacheado".
_MISS = object()


def cached(key_fn, ttl: int = 300):
    """Cacheia retorno de funcao usando chave gerada por `key_fn(*args, **kwargs)`.

    Expoe `.invalidate(*args, **kwargs)` para limpeza manual.

    Exemplo::

        @cached(lambda: 'branding_dict', ttl=600)
        def branding_dict():
            return {c.chave: c.valor for c in Configuracao.objects.all()}

        branding_dict.invalidate()
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs) if callable(key_fn) else key_fn
            cached_val = cache.get(key, _MISS)
            if cached_val is not _MISS:
                return cached_val
            result = fn(*args, **kwargs)
            cache.set(key, result, ttl)
            return result

        def invalidate(*args, **kwargs):
            key = key_fn(*args, **kwargs) if callable(key_fn) else key_fn
            cache.delete(key)

        wrapper.invalidate = invalidate
        return wrapper
    return decorator


def cache_get_or_set(key: str, factory, ttl: int = 300):
    """Versao funcional do cached() — sem decorator.

    Util quando cache eh usado uma unica vez ou key depende de runtime.
    """
    val = cache.get(key, _MISS)
    if val is not _MISS:
        return val
    val = factory()
    cache.set(key, val, ttl)
    return val
