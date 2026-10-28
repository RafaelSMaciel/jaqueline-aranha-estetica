"""Telefone BR — fonte unica de normalizacao, validacao, E.164 e mascara.

O telefone e a identidade da cliente no agendamento e no OTP: toda regra sobre ele mora aqui.

- Forma canonica (gravada em Cliente.telefone, chave do OTP e das buscas): so digitos, DDD +
  numero, sem DDI 55 e sem zero de tronco — '17999990001' (celular) ou '1733330000' (fixo).
  O CHECK ck_cliente_telefone_formato do Postgres exige 10-11 digitos.
- E.164 sem '+' (envio por SMS/WhatsApp): '55' + canonica, so para telefone BR valido —
  numero estrangeiro, curto ou lixo nunca vira destino (evita SMS pumping).
- href_tel: '+55...' para link de discagem; mascarar: '17****0001' para log.

DDD 55 (RS) nao e confundido com o DDI: '55999990000' tem 11 digitos e ja e canonico.
"""
import re

from django.core.exceptions import ValidationError

DDI_BR = '55'
MSG_INVALIDO = 'Informe DDD + número (10 ou 11 dígitos).'

_NAO_DIGITO = re.compile(r'\D+')
_CELULAR = re.compile(r'[1-9]{2}9\d{8}')


def normalizar(valor) -> str:
    """So digitos, sem DDI 55 e sem zero de tronco. NAO valida o tamanho (use validar ou
    valido_ou_vazio na borda). '+55 (17) 99999-0001' -> '17999990001'; '017 3333-0000' -> '1733330000'."""
    digitos = _NAO_DIGITO.sub('', str(valor or ''))
    if len(digitos) in (12, 13) and digitos.startswith(DDI_BR):
        digitos = digitos[2:]
    if len(digitos) in (11, 12) and digitos.startswith('0'):
        digitos = digitos[1:]
    return digitos


def _canonico_valido(digitos: str) -> bool:
    return len(digitos) in (10, 11) and 11 <= int(digitos[:2]) <= 99


def validar(valor) -> str:
    """Forma canonica ou ValidationError com mensagem pronta para a cliente."""
    digitos = normalizar(valor)
    if not _canonico_valido(digitos):
        raise ValidationError(MSG_INVALIDO, code='invalid_phone')
    return digitos


def valido_ou_vazio(valor) -> str:
    """Forma canonica ou '' se nao for telefone BR valido (fixo ou celular)."""
    digitos = normalizar(valor)
    return digitos if _canonico_valido(digitos) else ''


def eh_celular(valor) -> bool:
    """Celular BR (DDD + 9 + 8 digitos). SMS para fixo nunca chega e so gasta quota."""
    return bool(_CELULAR.fullmatch(normalizar(valor)))


def e164(valor) -> str:
    """Destino de SMS/WhatsApp: '55' + canonica, sem '+'. '' se nao for telefone BR valido."""
    digitos = valido_ou_vazio(valor)
    return f'{DDI_BR}{digitos}' if digitos else ''


def href_tel(valor) -> str:
    """'(17) 3222-1111' -> '+551732221111' (link tel:). '' se nao for telefone BR valido."""
    destino = e164(valor)
    return f'+{destino}' if destino else ''


def mascarar(valor) -> str:
    """Telefone para log: '17999990000' -> '17****0000' (DDD e final para depurar, meio oculto)."""
    if not valor:
        return ''
    digitos = _NAO_DIGITO.sub('', str(valor))
    if len(digitos) < 6:
        return '***'
    return f'{digitos[:2]}****{digitos[-4:]}'
