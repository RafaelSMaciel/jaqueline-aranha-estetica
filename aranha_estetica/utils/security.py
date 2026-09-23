"""Utilitarios de seguranca: masking de PII, comparacoes time-constant, IP do cliente."""
import hmac
import ipaddress
import re

from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme


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


IP_FALLBACK = "0.0.0.0"


def _ip_valido(valor) -> str:
    """Normaliza e valida um IP (v4/v6); '' se vazio/invalido."""
    valor = (valor or "").split(",")[0].strip()
    if not valor:
        return ""
    try:
        return str(ipaddress.ip_address(valor))
    except ValueError:
        return ""


def _meta_key(header: str) -> str:
    """Aceita 'X-Real-IP' ou 'HTTP_X_REAL_IP' e devolve a chave do request.META."""
    header = (header or "").strip()
    if not header or header == "REMOTE_ADDR" or header.startswith("HTTP_"):
        return header
    return "HTTP_" + header.upper().replace("-", "_")


def client_ip(request) -> str:
    """IP real do cliente — fonte unica p/ rate-limit, axes, OTP e auditoria.

    Atras do edge do Railway, REMOTE_ADDR e o IP do PROXY (igual p/ todos os
    visitantes) e o IP do cliente chega no header X-Real-IP, escrito pelo
    proprio edge. Por isso:
      1. `settings.CLIENT_IP_HEADER` (prod: 'HTTP_X_REAL_IP'), se presente e valido;
      2. senao REMOTE_ADDR (dev/testes: CLIENT_IP_HEADER vazio, nao forjavel);
      3. senao '0.0.0.0'.
    X-Forwarded-For NAO e usado: o cliente controla o valor (forjavel).
    Nunca devolve string vazia/invalida: o django-ratelimit faz
    ip_network(f'{ip}/32') e o Postgres (inet) rejeitaria lixo com 500.
    """
    if request is None:
        return IP_FALLBACK
    meta = getattr(request, "META", None) or {}
    header = _meta_key(getattr(settings, "CLIENT_IP_HEADER", ""))
    if header:
        ip = _ip_valido(meta.get(header))
        if ip:
            return ip
    return _ip_valido(meta.get("REMOTE_ADDR")) or IP_FALLBACK


def safe_next(request, raw, fallback="aranha:painel_overview"):
    """Valida ?next=/POST next contra open redirect; cai no fallback se externo/invalido."""
    if raw and url_has_allowed_host_and_scheme(
        raw, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return raw
    return fallback
