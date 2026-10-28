"""RRULE (RFC 5545) restrita ao subconjunto que a agenda sabe expandir.

Subconjunto (o mesmo que ``ck_agenda_excecao_regra_recorrencia_suportada``
exige no Postgres e que ``fn_ocorrencias_recorrencia`` expande — spec
docs/specs/padrao-sql.md §2.4/§4.a/§5.5):

    FREQ=DAILY|WEEKLY [;INTERVAL=2..99] [;BYDAY=MO,TU,...]          (sem ordinal)
    FREQ=MONTHLY      [;INTERVAL=2..99] [;BYDAY=[-1|1..4]MO,... | ;BYMONTHDAY=1..31]

DTSTART = inicio do bloqueio (hora LOCAL). A forma canonica e
``FREQ[;INTERVAL][;BYDAY|;BYMONTHDAY]``, sem ``RRULE:``, INTERVAL=1 omitido,
BYDAY ordenado (MO..SU) e sem repeticao.

COUNT/UNTIL nao ficam na regra: o fim vira data (``data_fim_recorrencia``, ultimo
dia em que uma ocorrencia pode comecar). Com o DTSTART conhecido a conversao e
exata (a regra gera no maximo uma ocorrencia por dia). Ordinal em BYDAY de
DAILY/WEEKLY e descartado (o dateutil ja o ignorava) e WKST so e aceito = MO.

Usado pela migration 0048 (normaliza as regras legadas dos bloqueios):
mudanca aqui precisa continuar produzindo texto aceito pela regex do PG.
"""
import itertools
import re
from datetime import date, datetime, time, timezone as dt_timezone

from django.utils import timezone

DIAS = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')
FREQS = ('DAILY', 'WEEKLY', 'MONTHLY')
CHAVES = ('FREQ', 'INTERVAL', 'BYDAY', 'BYMONTHDAY', 'WKST', 'COUNT', 'UNTIL')
MAX_COUNT = 100_000

_RE_BYDAY = re.compile(r'^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$')
_RE_UNTIL = re.compile(r'^(\d{8})(T(\d{6})(Z)?)?$')

# Regex do CHECK so-Postgres (0048) — espelho do que normalizar() produz.
REGEX_SUBCONJUNTO = (
    r'^(FREQ=(DAILY|WEEKLY)(;INTERVAL=[1-9][0-9]?)?(;BYDAY=(MO|TU|WE|TH|FR|SA|SU)(,(MO|TU|WE|TH|FR|SA|SU))*)?'
    r'|FREQ=MONTHLY(;INTERVAL=[1-9][0-9]?)?(;BYDAY=(-1|[1-4])?(MO|TU|WE|TH|FR|SA|SU)(,(-1|[1-4])?(MO|TU|WE|TH|FR|SA|SU))*'
    r'|;BYMONTHDAY=([1-9]|[12][0-9]|3[01]))?)$'
)


class RegraNaoSuportada(ValueError):
    """Regra fora do subconjunto (ou invalida)."""


def _inteiro(valor, chave, minimo, maximo):
    if not re.fullmatch(r'[+-]?\d+', valor or ''):
        raise RegraNaoSuportada(f'{chave}={valor!r} nao e inteiro')
    n = int(valor)
    if not minimo <= n <= maximo:
        raise RegraNaoSuportada(f'{chave}={n} fora de {minimo}..{maximo}')
    return n


def _partes(regra):
    texto = regra.strip()
    if '\n' in texto or '\r' in texto:
        raise RegraNaoSuportada('regra com mais de uma linha (DTSTART/EXDATE/RDATE nao suportados)')
    if texto.upper().startswith('RRULE:'):
        texto = texto[len('RRULE:'):]
    partes = {}
    for bruto in texto.upper().replace(' ', '').split(';'):
        if not bruto:
            continue
        chave, sep, valor = bruto.partition('=')
        if not sep or not valor:
            raise RegraNaoSuportada(f'parte invalida: {bruto!r}')
        if chave not in CHAVES:
            raise RegraNaoSuportada(f'{chave} nao suportado (so {", ".join(CHAVES[:4])})')
        if chave in partes:
            raise RegraNaoSuportada(f'{chave} repetido')
        partes[chave] = valor
    return partes


def _byday(valor, freq):
    itens = set()
    for item in valor.split(','):
        m = _RE_BYDAY.match(item)
        if not m:
            raise RegraNaoSuportada(f'BYDAY invalido: {item!r}')
        ordinal = int(m.group(1)) if m.group(1) else None
        if freq != 'MONTHLY':
            ordinal = None  # RFC 5545: ordinal so em MONTHLY/YEARLY (dateutil ignora)
        elif ordinal is not None and ordinal not in (-1, 1, 2, 3, 4):
            raise RegraNaoSuportada(f'BYDAY {item}: ordinal so -1 ou 1..4')
        itens.add((DIAS.index(m.group(2)), ordinal))
    return ','.join(
        f'{"" if o is None else o}{DIAS[d]}'
        for d, o in sorted(itens, key=lambda x: (x[0], -99 if x[1] is None else x[1]))
    )


def _dtstart_local(dtstart):
    """DTSTART do motor (hora local, naive) — o mesmo que AgendaExcecao.ocorrencias usa."""
    if isinstance(dtstart, datetime):
        if timezone.is_aware(dtstart):
            dtstart = timezone.localtime(dtstart)
        return dtstart.replace(tzinfo=None)
    return datetime.combine(dtstart, time.min)


def _until_local(valor):
    m = _RE_UNTIL.match(valor)
    if not m:
        raise RegraNaoSuportada(f'UNTIL invalido: {valor!r}')
    try:
        dia = datetime.strptime(m.group(1), '%Y%m%d')  # noqa: DTZ007 - motor rrule e naive local
        if not m.group(2):
            return datetime.combine(dia.date(), time.max)  # UNTIL em data: o dia inteiro vale
        instante = datetime.strptime(m.group(1) + m.group(3), '%Y%m%d%H%M%S')  # noqa: DTZ007 - idem; Z abaixo
    except ValueError as exc:
        raise RegraNaoSuportada(f'UNTIL invalido: {valor!r}') from exc
    if m.group(4):  # Z = UTC
        instante = timezone.localtime(instante.replace(tzinfo=dt_timezone.utc)).replace(tzinfo=None)
    return instante


def _fim_por_count_until(canonica, partes, dtstart):
    from dateutil.rrule import rrulestr

    inicio = _dtstart_local(dtstart)
    regra = rrulestr(f'RRULE:{canonica}', dtstart=inicio)
    if 'COUNT' in partes:
        n = _inteiro(partes['COUNT'], 'COUNT', 1, MAX_COUNT)
        ultima = None
        for ultima in itertools.islice(regra, n):
            pass
    else:
        ultima = regra.before(_until_local(partes['UNTIL']), inc=True)
    if ultima is None:
        # nenhuma ocorrencia: fim antes do DTSTART (a regra nao gera nada)
        return date.fromordinal(inicio.date().toordinal() - 1)
    return ultima.date()


def normalizar_regra_recorrencia(regra, dtstart=None):
    """(regra canonica ou None, limite em data derivado de COUNT/UNTIL ou None).

    Levanta RegraNaoSuportada para o que nao cabe no subconjunto. COUNT/UNTIL
    exigem ``dtstart`` (inicio do bloqueio) para virar limite em data.
    """
    if regra is None or not str(regra).strip():
        return None, None
    partes = _partes(str(regra))
    freq = partes.get('FREQ')
    if freq not in FREQS:
        raise RegraNaoSuportada(f'FREQ={freq!r} nao suportado (so {"/".join(FREQS)})')
    if partes.get('WKST', 'MO') != 'MO':
        raise RegraNaoSuportada('WKST diferente de MO nao suportado')
    if 'COUNT' in partes and 'UNTIL' in partes:
        raise RegraNaoSuportada('COUNT e UNTIL juntos (RFC 5545 proibe)')

    canonica = [f'FREQ={freq}']
    if 'INTERVAL' in partes:
        intervalo = _inteiro(partes['INTERVAL'], 'INTERVAL', 1, 99)
        if intervalo != 1:
            canonica.append(f'INTERVAL={intervalo}')
    if 'BYMONTHDAY' in partes:
        if freq != 'MONTHLY':
            raise RegraNaoSuportada('BYMONTHDAY so com FREQ=MONTHLY')
        if 'BYDAY' in partes:
            raise RegraNaoSuportada('BYDAY e BYMONTHDAY juntos nao suportado')
        canonica.append(f'BYMONTHDAY={_inteiro(partes["BYMONTHDAY"], "BYMONTHDAY", 1, 31)}')
    elif 'BYDAY' in partes:
        canonica.append(f'BYDAY={_byday(partes["BYDAY"], freq)}')
    canonica = ';'.join(canonica)

    try:
        from dateutil.rrule import rrulestr
        rrulestr(f'RRULE:{canonica}', dtstart=datetime(2000, 1, 3))  # noqa: DTZ001 - so valida a sintaxe
    except (ValueError, TypeError) as exc:  # defensivo: o subconjunto sempre parseia
        raise RegraNaoSuportada(f'dateutil recusou {canonica!r}: {exc}') from exc
    if not re.fullmatch(REGEX_SUBCONJUNTO, canonica):
        raise RegraNaoSuportada(f'{canonica!r} fora da regex do banco')

    limite = None
    if 'COUNT' in partes or 'UNTIL' in partes:
        if dtstart is None:
            raise RegraNaoSuportada('COUNT/UNTIL: informe o fim em "fim da recorrencia"')
        limite = _fim_por_count_until(canonica, partes, dtstart)
    return canonica, limite
