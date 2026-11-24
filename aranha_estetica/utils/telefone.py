"""Telefone BR — fonte unica de normalizacao, validacao, E.164 e mascara.

O telefone e a identidade da cliente no agendamento e no OTP: toda regra sobre ele mora aqui.

- Forma canonica (gravada em Cliente.telefone, chave do OTP e das buscas): so digitos, DDD +
  numero, sem DDI 55 e sem zero de tronco — '17999990001' (celular) ou '1733330000' (fixo).
  O CHECK ck_cliente_telefone_formato do Postgres exige 10-11 digitos.
- E.164 sem '+' (envio por SMS/WhatsApp): '55' + canonica, so para telefone BR valido —
  numero estrangeiro, curto ou lixo nunca vira destino (evita SMS pumping).
- href_tel: '+55...' para link de discagem; formatar: '(17) 99999-0001' para exibir;
  mascarar: '17****0001' para log.

DDD 55 (RS) nao e confundido com o DDI: '55999990000' tem 11 digitos e ja e canonico.

Qualidade do dado: o DDD precisa existir (os 67 codigos nacionais da Anatel — antes '00', '10',
'20' passavam) e o celular nao pode ser um numero de enfeite ('(11) 99999-9999', '90000-0000':
os 8 digitos depois do 9 todos iguais). Nao adivinha operadora nem portabilidade: so barra o que
com certeza nao e telefone de ninguem.
"""
import re

from django.core.exceptions import ValidationError

DDI_BR = '55'
MSG_INVALIDO = 'Informe DDD + número (10 ou 11 dígitos).'

_NAO_DIGITO = re.compile(r'\D+')
_CELULAR = re.compile(r'[1-9]{2}9\d{8}')

# Codigos nacionais (DDD) em uso — Plano Geral de Codigos Nacionais da Anatel (67 areas).
DDDS_VALIDOS = frozenset({
    11, 12, 13, 14, 15, 16, 17, 18, 19,            # SP
    21, 22, 24, 27, 28,                            # RJ, ES
    31, 32, 33, 34, 35, 37, 38,                    # MG
    41, 42, 43, 44, 45, 46, 47, 48, 49,            # PR, SC
    51, 53, 54, 55,                                # RS
    61, 62, 63, 64, 65, 66, 67, 68, 69,            # DF, GO, TO, MT, MS, AC, RO
    71, 73, 74, 75, 77, 79,                        # BA, SE
    81, 82, 83, 84, 85, 86, 87, 88, 89,            # PE, AL, PB, RN, CE, PI
    91, 92, 93, 94, 95, 96, 97, 98, 99,            # PA, AM, RR, AP, MA
})


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
    return len(digitos) in (10, 11) and int(digitos[:2]) in DDDS_VALIDOS


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
    """Celular BR (DDD existente + 9 + 8 digitos). SMS para fixo nunca chega e so gasta quota;
    numero de enfeite (os 8 digitos finais iguais: 99999-9999, 90000-0000) tambem nao."""
    digitos = normalizar(valor)
    if not _CELULAR.fullmatch(digitos) or not _canonico_valido(digitos):
        return False
    return len(set(digitos[3:])) > 1


def e164(valor) -> str:
    """Destino de SMS/WhatsApp: '55' + canonica, sem '+'. '' se nao for telefone BR valido."""
    digitos = valido_ou_vazio(valor)
    return f'{DDI_BR}{digitos}' if digitos else ''


def href_tel(valor) -> str:
    """'(17) 3222-1111' -> '+551732221111' (link tel:). '' se nao for telefone BR valido."""
    destino = e164(valor)
    return f'+{destino}' if destino else ''


def formatar(valor) -> str:
    """Telefone para exibir: '17999990001' -> '(17) 99999-0001', '1733330000' -> '(17) 3333-0000'.
    Fora do padrao BR (estrangeiro, lixo) volta como veio, sem perder o que foi digitado."""
    digitos = valido_ou_vazio(valor)
    if not digitos:
        return '' if valor is None else str(valor)
    return f'({digitos[:2]}) {digitos[2:-4]}-{digitos[-4:]}'


def mascarar(valor) -> str:
    """Telefone para log: '17999990000' -> '17****0000' (DDD e final para depurar, meio oculto)."""
    if not valor:
        return ''
    digitos = _NAO_DIGITO.sub('', str(valor))
    if len(digitos) < 6:
        return '***'
    return f'{digitos[:2]}****{digitos[-4:]}'
