"""Sessao da cliente verificada — uma so para o site inteiro (spec regras-negocio-registry, item 3).

Qualquer codigo SMS confirmado (no wizard de agendamento ou no "Meus agendamentos") abre a mesma
sessao, presa ao TELEFONE que recebeu o codigo (e-mail nunca e identidade). Antes eram duas: a do
wizard (30 min) e a do portal (1 h), com chaves proprias — quem acabava de entrar na area da cliente
pedia outro SMS para agendar.

O que cada parte exige da sessao:
- Agendar pelo site: verificacao de ate 30 min que ainda nao foi usada num pedido (cada pedido
  online pede um SMS novo: ``registrar_agendamento`` gasta a verificacao).
- "Meus agendamentos": a sessao aberta (1 h, renovada a cada request em producao).
- Baixar os proprios dados: verificacao de ate 10 min.
- Lista de espera: verificacao de ate 30 min liga a inscricao ao cadastro daquele celular.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

SESSAO_TELEFONE = 'cliente_telefone'
SESSAO_VERIFICADO_EM = 'cliente_verificado_em'
# O pedido online ja usou a verificacao atual: o proximo agendamento pede outro SMS.
SESSAO_AGENDOU = 'cliente_agendou'
# Chaves das duas sessoes antigas (wizard e portal): saem quando a sessao nova abre ou encerra.
_LEGADAS = ('otp_agendamento_telefone', 'otp_agendamento_expira', 'meus_agendamentos_telefone',
            'meus_agendamentos_verificado_em', 'meus_agendamentos_email')

DURACAO_SESSAO_SEGUNDOS = 3600
VALIDADE_AGENDAMENTO = timedelta(minutes=30)
JANELA_DADOS = timedelta(minutes=10)


def registrar_verificacao(request, telefone: str) -> None:
    """Codigo SMS confirmado: abre (ou renova) a sessao da cliente. Anti session-fixation (cycle_key)."""
    request.session.cycle_key()
    for chave in _LEGADAS:
        request.session.pop(chave, None)
    request.session[SESSAO_TELEFONE] = telefone
    request.session[SESSAO_VERIFICADO_EM] = timezone.now().isoformat()
    request.session.pop(SESSAO_AGENDOU, None)
    request.session.set_expiry(DURACAO_SESSAO_SEGUNDOS)


def telefone(request) -> str:
    """Telefone (digitos) da sessao aberta, ou ''."""
    return request.session.get(SESSAO_TELEFONE) or ''


def _verificado_em(request) -> datetime | None:
    valor = request.session.get(SESSAO_VERIFICADO_EM)
    if not valor:
        return None
    try:
        momento = datetime.fromisoformat(valor)
    except (TypeError, ValueError):
        return None
    return timezone.make_aware(momento) if timezone.is_naive(momento) else momento


def verificado_ha_no_maximo(request, janela: timedelta) -> bool:
    """A cliente confirmou o codigo ha no maximo `janela`."""
    momento = _verificado_em(request)
    return bool(telefone(request)) and momento is not None and timezone.now() - momento <= janela


def telefone_verificado(request, janela: timedelta = VALIDADE_AGENDAMENTO) -> str:
    """Telefone confirmado ha no maximo `janela`, ou ''."""
    return telefone(request) if verificado_ha_no_maximo(request, janela) else ''


def telefone_para_agendamento(request) -> str:
    """Telefone que pode fazer um pedido pelo site agora (verificacao recente e ainda nao usada), ou ''."""
    if request.session.get(SESSAO_AGENDOU):
        return ''
    return telefone_verificado(request, VALIDADE_AGENDAMENTO)


def registrar_agendamento(request) -> None:
    """Pedido online aceito: a area da cliente continua aberta, mas o proximo pedido pede outro SMS."""
    request.session[SESSAO_AGENDOU] = True


def encerrar(request) -> None:
    """Sair: fecha a sessao da cliente (o resto da sessao do navegador continua)."""
    for chave in (SESSAO_TELEFONE, SESSAO_VERIFICADO_EM, SESSAO_AGENDOU, *_LEGADAS):
        request.session.pop(chave, None)
