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

from .utils import telefone

_NAO_DIGITO_RE = re.compile(r"\D+")


def _clean_digits(value: str) -> str:
    return _NAO_DIGITO_RE.sub("", value or "")


# ─── Telefone ───────────────────────────────────────────────────────────
# A regra mora em utils/telefone (fonte unica). Estes nomes ficam porque migrations e o model
# Cliente os importam (validate_telefone_br no campo, normalizar_telefone no save).

def normalizar_telefone(value: str) -> str:
    """Forma canonica de telefone BR (utils/telefone.normalizar): so digitos, sem DDI e sem zero
    de tronco. NAO valida tamanho — use validate_telefone_br() ou utils/telefone na borda."""
    return telefone.normalizar(value)


def validate_telefone_br(value: str) -> None:
    """Validator de campo: telefone BR (DDD + 8 ou 9 digitos). Aceita mascara e +55. Vazio passa."""
    if value:
        telefone.validar(value)


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
