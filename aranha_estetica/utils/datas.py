"""Helpers de data/hora no fuso da clinica (settings.TIME_ZONE).

Com USE_TZ=True o banco devolve datetimes em UTC. Qualquer texto exibido ao
cliente/equipe (e-mail, WhatsApp, SMS, push, Excel) ou calculo de "hoje"
deve passar por aqui — `dt.strftime()` direto ou `timezone.now().date()`
mostram/calculam em UTC (+3h / dia seguinte apos as 21h em BRT). O ruff (DTZ) barra
date.today()/datetime.now() sem fuso no CI.
"""
from datetime import date, datetime, time, timedelta

from django.utils import timezone


def hoje() -> date:
    """Data de hoje no fuso local (nao UTC)."""
    return timezone.localdate()


# Janelas das telas de relatorio do painel (NPS, comissoes), em dias; 'tudo' = sem corte.
PERIODOS = {'30': 30, '90': 90, 'tudo': None}
PERIODO_PADRAO = '90'


def periodo_valido(periodo: str | None) -> str:
    """O periodo pedido (?periodo=) ou o padrao, se nao for um dos aceitos."""
    return periodo if periodo in PERIODOS else PERIODO_PADRAO


def inicio_periodo(periodo: str) -> datetime | None:
    """Instante de corte da janela (None = 'tudo')."""
    dias = PERIODOS[periodo_valido(periodo)]
    return None if dias is None else timezone.now() - timedelta(days=dias)


def inicio_do_dia(dia: date) -> datetime:
    """Meia-noite local (aware) de uma data — limite de filtro em DateTimeField."""
    return timezone.make_aware(datetime.combine(dia, time.min))


def dias_locais(campo: str, de: date, ate: date | None = None) -> dict:
    """Filtro dos dias locais `de`..`ate` (inclusive) num DateTimeField, como intervalo:
    ``.filter(**dias_locais('data_hora_inicio', hoje()))``.

    `campo__date=` da o mesmo resultado, mas converte cada linha p/ o fuso no SQL e nao usa o
    indice da coluna (agenda, financeiro e auditoria filtram por dia o tempo todo).
    """
    fim = (ate or de) + timedelta(days=1)
    return {f'{campo}__gte': inicio_do_dia(de), f'{campo}__lt': inicio_do_dia(fim)}


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


def ler_data(valor: str) -> date:
    """'AAAA-MM-DD' (input type=date, query string) -> date "de parede". ValueError se invalido.

    O instante sai de timezone.make_aware(datetime.combine(dia, hora)) no fuso da clinica.
    """
    return datetime.strptime(valor, '%Y-%m-%d').date()  # noqa: DTZ007 - so a data


def ler_mes(valor: str) -> date:
    """'AAAA-MM' (input type=month, ?mes=) -> 1o dia do mes. ValueError se invalido."""
    return datetime.strptime(valor, '%Y-%m').date()  # noqa: DTZ007 - so a data


def ler_hora(valor: str) -> time:
    """'HH:MM' ou 'HH:MM:SS' (input type=time) -> time. ValueError se invalido."""
    for formato in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(valor, formato).time()  # noqa: DTZ007 - so a hora
        except ValueError:
            continue
    raise ValueError(f'hora invalida: {valor!r}')


def ler_instante_iso(valor) -> datetime:
    """ISO 8601 do navegador (FullCalendar, toISOString) -> datetime aware no fuso local.

    Aceita o 'Z' de UTC; sem offset vale o fuso da clinica (naive deslocaria o horario salvo).
    ValueError se nao for texto ISO valido (None/numero/lista tambem)."""
    if not isinstance(valor, str):
        raise ValueError(f'instante invalido: {valor!r}')
    dt = datetime.fromisoformat(valor.strip().replace('Z', '+00:00'))
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return timezone.localtime(dt)
