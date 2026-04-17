"""Validadores reutilizaveis (nivel Python). Aplique via `validators=[...]` em fields ou chame em Form.clean()."""
import re
from datetime import date
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


_CPF_RE = re.compile(r"\D+")


def _clean_digits(value: str) -> str:
    return _CPF_RE.sub("", value or "")


def validate_cpf(value: str) -> None:
    """Valida CPF por modulo 11. Aceita mascarado ou so digitos."""
    if value is None or value == "":
        return
    digits = _clean_digits(value)
    if len(digits) != 11 or digits == digits[0] * 11:
        raise ValidationError(_("CPF invalido."), code="invalid_cpf")

    def dv(base: str, peso_inicial: int) -> int:
        total = sum(int(d) * p for d, p in zip(base, range(peso_inicial, 1, -1)))
        resto = (total * 10) % 11
        return 0 if resto == 10 else resto

    if dv(digits[:9], 10) != int(digits[9]) or dv(digits[:10], 11) != int(digits[10]):
        raise ValidationError(_("CPF invalido."), code="invalid_cpf")


def validate_telefone_br(value: str) -> None:
    """Valida telefone BR (10 ou 11 digitos, com DDD). Aceita mascara."""
    if not value:
        return
    digits = _clean_digits(value)
    if len(digits) not in (10, 11):
        raise ValidationError(_("Telefone deve ter 10 ou 11 digitos com DDD."), code="invalid_phone")
    ddd = int(digits[:2])
    if ddd < 11 or ddd > 99:
        raise ValidationError(_("DDD invalido."), code="invalid_phone")


def validate_data_nascimento(value: date) -> None:
    """Data de nascimento deve ser no passado e ate 150 anos atras."""
    if value is None:
        return
    hoje = date.today()
    if value > hoje:
        raise ValidationError(_("Data de nascimento nao pode ser futura."), code="invalid_birth")
    if (hoje.year - value.year) > 150:
        raise ValidationError(_("Data de nascimento invalida."), code="invalid_birth")


def validate_maior_idade(value: date) -> None:
    """Exige maior de 18 anos."""
    if value is None:
        return
    hoje = date.today()
    idade = hoje.year - value.year - ((hoje.month, hoje.day) < (value.month, value.day))
    if idade < 18:
        raise ValidationError(_("Cadastro permitido apenas para maiores de 18 anos."), code="underage")


def validate_valor_positivo(value) -> None:
    if value is None:
        return
    if value < 0:
        raise ValidationError(_("Valor nao pode ser negativo."), code="negative_value")
