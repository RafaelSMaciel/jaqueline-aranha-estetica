"""Helpers de data/hora no fuso da clinica (settings.TIME_ZONE).

Com USE_TZ=True o banco devolve datetimes em UTC. Qualquer texto exibido ao
cliente/equipe (e-mail, WhatsApp, SMS, push, Excel) ou calculo de "hoje"
deve passar por aqui — `dt.strftime()` direto ou `timezone.now().date()`
mostram/calculam em UTC (+3h / dia seguinte apos as 21h em BRT).
"""
from datetime import date, datetime

from django.utils import timezone


def agora_local() -> datetime:
    """Datetime aware no fuso local."""
    return timezone.localtime(timezone.now())


def hoje() -> date:
    """Data de hoje no fuso local (nao UTC)."""
    return timezone.localdate()


def local(dt: datetime | None) -> datetime | None:
    """Converte datetime aware p/ o fuso local (naive e devolvido como esta)."""
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return dt
    return timezone.localtime(dt)


def fmt_local(dt: datetime | None, fmt: str = '%d/%m/%Y %H:%M') -> str:
    """Formata datetime no fuso local; '' se None."""
    if dt is None:
        return ''
    return local(dt).strftime(fmt)


def data_local(dt: datetime | None) -> date | None:
    """Data (no fuso local) de um datetime aware."""
    if dt is None:
        return None
    return local(dt).date()
