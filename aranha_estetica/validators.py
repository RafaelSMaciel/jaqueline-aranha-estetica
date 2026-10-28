"""Validadores e normalizadores reutilizaveis (nivel Python).

Aplique via `validators=[...]` em fields ou chame na borda (view/form) ANTES
de gravar: normalizar_* (forma canonica) + validate_* (ValidationError). O Postgres
tem CHECK de formato em cliente.telefone (10-11 digitos) e cliente.cpf (11
digitos): dado invalido que chega ao banco vira IntegrityError (500).

validate_cpf, validate_data_nascimento, validate_telefone_br e normalizar_telefone
sao referenciados por migrations: nao renomear nem remover.
"""
import re
from datetime import date

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


_NAO_DIGITO_RE = re.compile(r"\D+")


def _clean_digits(value: str) -> str:
    return _NAO_DIGITO_RE.sub("", value or "")


# ─── Telefone ───────────────────────────────────────────────────────────

def normalizar_telefone(value: str) -> str:
    """Forma canonica de telefone BR: so digitos, sem DDI (+55) e sem zero de tronco.

    '+55 (17) 99999-0001' -> '17999990001'; '017 3333-0000' -> '1733330000'.
    DDD 55 (RS) nao e afetado: '55999990000' tem 11 digitos e fica como esta.
    NAO valida tamanho — use validate_telefone_br() na borda.
    Telefone e chave natural do booking publico: toda escrita (Cliente.save)
    e todo lookup passam por aqui. DDI p/ envio e responsabilidade de
    utils/sms e utils/whatsapp, nao do armazenamento.
    """
    d = _clean_digits(value or '')
    if len(d) in (12, 13) and d.startswith('55'):
        d = d[2:]
    if len(d) in (11, 12) and d.startswith('0'):
        d = d[1:]
    return d


def validate_telefone_br(value: str) -> None:
    """Valida telefone BR (10 ou 11 digitos com DDD). Aceita mascara e +55."""
    if not value:
        return
    digits = normalizar_telefone(value)
    if len(digits) not in (10, 11):
        raise ValidationError(_("Telefone deve ter 10 ou 11 dígitos com DDD."), code="invalid_phone")
    ddd = int(digits[:2])
    if ddd < 11 or ddd > 99:
        raise ValidationError(_("DDD inválido."), code="invalid_phone")


# ─── CPF ────────────────────────────────────────────────────────────────

def normalizar_cpf(value: str) -> str:
    """Forma canonica de CPF: somente digitos (sem mascara)."""
    return _clean_digits(value or '')


def validate_cpf(value: str) -> None:
    """Valida CPF por modulo 11. Aceita mascarado ou so digitos."""
    if value is None or value == "":
        return
    digits = _clean_digits(value)
    if len(digits) != 11 or digits == digits[0] * 11:
        raise ValidationError(_("CPF inválido."), code="invalid_cpf")

    def dv(base: str, peso_inicial: int) -> int:
        total = sum(int(d) * p for d, p in zip(base, range(peso_inicial, 1, -1), strict=False))
        resto = (total * 10) % 11
        return 0 if resto == 10 else resto

    if dv(digits[:9], 10) != int(digits[9]) or dv(digits[:10], 11) != int(digits[10]):
        raise ValidationError(_("CPF inválido."), code="invalid_cpf")


# ─── Datas ──────────────────────────────────────────────────────────────

def validate_data_nascimento(value: date) -> None:
    """Data de nascimento deve ser no passado e ate 150 anos atras."""
    if value is None:
        return
    hoje = timezone.localdate()
    if value > hoje:
        raise ValidationError(_("Data de nascimento não pode ser futura."), code="invalid_birth")
    if (hoje.year - value.year) > 150:
        raise ValidationError(_("Data de nascimento inválida."), code="invalid_birth")
