"""Utilitarios de seguranca: masking de PII e comparacoes time-constant."""
import hmac
import re


def mask_email(email: str) -> str:
    """Mascara email para logs: rafa***@gmail.com"""
    if not email or "@" not in email:
        return email or ""
    user, domain = email.split("@", 1)
    if len(user) <= 2:
        masked_user = user[:1] + "*"
    else:
        masked_user = user[:3] + "*" * max(1, len(user) - 3)
    return f"{masked_user}@{domain}"


def mask_cpf(cpf: str) -> str:
    """Mascara CPF: 123.***.***-45"""
    if not cpf:
        return ""
    digits = re.sub(r"\D+", "", cpf)
    if len(digits) != 11:
        return "***"
    return f"{digits[:3]}.***.***-{digits[9:]}"


def mask_telefone(tel: str) -> str:
    """Mascara telefone: +55 (11) *****-3210"""
    if not tel:
        return ""
    digits = re.sub(r"\D+", "", tel)
    if len(digits) < 8:
        return "***"
    return f"***-{digits[-4:]}"


def safe_str_compare(a: str, b: str) -> bool:
    """Comparacao time-constant (previne timing attacks em tokens/OTP)."""
    if a is None or b is None:
        return False
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def client_ip(request) -> str:
    """Obtem IP real do cliente considerando proxy reverso."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")
