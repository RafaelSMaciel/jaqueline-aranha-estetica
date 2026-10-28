"""Validadores e normalizadores reutilizaveis (nivel Python).

Aplique via `validators=[...]` em fields ou chame na borda (view/form) ANTES
de gravar. O Postgres tem CHECK de formato em cliente.telefone (10-11
digitos) e cliente.cpf (11 digitos): dado invalido que chega ao banco vira
IntegrityError (500). API p/ as views:

    tel = validar_telefone(request.POST.get('telefone'))   # str canonica ou ValidationError
    if not telefone_valido(valor): ...                      # checagem booleana
    cpf = validar_cpf(request.POST.get('cpf'))              # str canonica, None se vazio, ou ValidationError
"""
import re
from datetime import date

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .utils import telefone

_NAO_DIGITO_RE = re.compile(r"\D+")

MSG_TELEFONE_INVALIDO = telefone.MSG_INVALIDO
MSG_CPF_INVALIDO = 'CPF inválido.'


def _clean_digits(value: str) -> str:
    return _NAO_DIGITO_RE.sub("", value or "")


# ─── Telefone ───────────────────────────────────────────────────────────
# A regra mora em utils/telefone (fonte unica). Estes nomes ficam porque migrations e o model
# Cliente os importam (validate_telefone_br no campo, normalizar_telefone no save).

def normalizar_telefone(value: str) -> str:
    """Forma canonica de telefone BR (utils/telefone.normalizar): so digitos, sem DDI e sem zero
    de tronco. NAO valida tamanho — use telefone_valido()/validar_telefone() na borda."""
    return telefone.normalizar(value)


def validate_telefone_br(value: str) -> None:
    """Validator de campo: telefone BR (DDD + 8 ou 9 digitos). Aceita mascara e +55. Vazio passa."""
    if value:
        telefone.validar(value)


def telefone_valido(value: str) -> bool:
    """True se `value` (qualquer mascara) normaliza p/ telefone BR valido."""
    return bool(telefone.valido_ou_vazio(value))


def validar_telefone(value: str) -> str:
    """Forma canonica ou ValidationError com MSG_TELEFONE_INVALIDO (pronta p/ o usuario)."""
    return telefone.validar(value)


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


def cpf_valido(value: str) -> bool:
    """True se `value` e CPF valido (digitos verificadores conferem)."""
    if not value:
        return False
    try:
        validate_cpf(value)
    except ValidationError:
        return False
    return True


def validar_cpf(value: str) -> str | None:
    """CPF opcional: None se vazio; forma canonica se valido; senao ValidationError."""
    if not (value or '').strip():
        return None
    if not cpf_valido(value):
        raise ValidationError(MSG_CPF_INVALIDO, code="invalid_cpf")
    return normalizar_cpf(value)


# ─── Datas / valores ────────────────────────────────────────────────────

def validate_data_nascimento(value: date) -> None:
    """Data de nascimento deve ser no passado e ate 150 anos atras."""
    if value is None:
        return
    hoje = timezone.localdate()
    if value > hoje:
        raise ValidationError(_("Data de nascimento não pode ser futura."), code="invalid_birth")
    if (hoje.year - value.year) > 150:
        raise ValidationError(_("Data de nascimento inválida."), code="invalid_birth")


def validate_maior_idade(value: date) -> None:
    """Exige maior de 18 anos."""
    if value is None:
        return
    hoje = timezone.localdate()
    idade = hoje.year - value.year - ((hoje.month, hoje.day) < (value.month, value.day))
    if idade < 18:
        raise ValidationError(_("Cadastro permitido apenas para maiores de 18 anos."), code="underage")


def validate_valor_positivo(value) -> None:
    if value is None:
        return
    if value < 0:
        raise ValidationError(_("Valor não pode ser negativo."), code="negative_value")
