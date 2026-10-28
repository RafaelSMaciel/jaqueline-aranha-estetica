"""Prontuario: quem pode ler/escrever o registro clinico de uma cliente (LGPD art. 11).

Regra unica do vinculo profissional -> prontuario, usada pelo painel (views/painel/prontuario:
decorator de acesso e botao de editar) e pelo portal do profissional (agenda com alertas de
saude, anotacao da sessao). Staff (ADMIN) ve tudo e nao passa por aqui.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from ..models import Atendimento

# Vinculo profissional -> prontuario (a dona pode ajustar as janelas).
JANELA_REALIZADO_DIAS = 180   # atendeu a cliente nos ultimos N dias
JANELA_FUTURO_DIAS = 60       # tem horario marcado nos proximos N dias


def vinculo_clinico(profissional, cliente, *, escrita: bool = False) -> Atendimento | None:
    """Atendimento que da a profissional acesso ao prontuario da cliente, ou None.

    Leitura: REALIZADO nos ultimos JANELA_REALIZADO_DIAS, ou PENDENTE/AGENDADO/
    CONFIRMADO entre ontem e +max(JANELA_FUTURO_DIAS, prof.max_advance_dias)
    (a profissional precisa ver os alertas de saude antes de aprovar um pedido;
    quem aceita reserva com 90 dias de antecedencia ve a ficha desde o pedido).
    Escrita: o mesmo, sem PENDENTE.
    CANCELADO/REAGENDADO/FALTOU nunca dao vinculo.
    """
    if profissional is None or not profissional.ativo:
        return None
    agora = timezone.now()
    janela_futuro = max(JANELA_FUTURO_DIAS, profissional.max_advance_dias or 0)
    ativos = [Atendimento.STATUS_AGENDADO, Atendimento.STATUS_CONFIRMADO]
    if not escrita:
        ativos.append(Atendimento.STATUS_PENDENTE)
    return (
        Atendimento.objects.filter(cliente=cliente, profissional=profissional)
        .filter(
            Q(status=Atendimento.STATUS_REALIZADO,
              data_hora_inicio__gte=agora - timedelta(days=JANELA_REALIZADO_DIAS))
            | Q(status__in=ativos,
                data_hora_inicio__gte=agora - timedelta(days=1),
                data_hora_inicio__lte=agora + timedelta(days=janela_futuro))
        )
        .order_by('-data_hora_inicio')
        .first()
    )
