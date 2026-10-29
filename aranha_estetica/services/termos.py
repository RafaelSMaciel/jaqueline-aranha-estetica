"""Termos de consentimento: quais versoes valem p/ um atendimento e o que falta aceitar.

Fonte unica da regra usada pela pagina publica do termo (/termo/<token>/),
pelo painel (compliance, link do termo, badge 'Termo pendente') e pelo portal
do profissional. Termos aplicaveis a um procedimento:
  - LGPD global (procedimento nulo);
  - termo de PROCEDIMENTO geral (procedimento nulo = vale p/ todos);
  - termo de PROCEDIMENTO do proprio procedimento.
LGPD presa a um procedimento (dado legado de antes da validacao do painel)
nao vale para ninguem.
"""
from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from ..models import AceiteTermo, Notificacao, VersaoTermo
from ..models.agendamentos import STATUS_ATIVOS

# Atendimento que ainda aceita assinatura de termo (e gera link novo)
STATUS_ACEITA_TERMO = STATUS_ATIVOS

# Tipo da Notificacao que carrega o link do termo (/termo/<token>, LinkAcesso TERMO)
TIPO_NOTIF_TERMO = 'TERMO'
Q_NOTIF_TERMO = Q(tipo=TIPO_NOTIF_TERMO)


def q_termos_aplicaveis(procedimento_id) -> Q:
    return (
        Q(tipo='LGPD', procedimento__isnull=True)
        | Q(tipo='PROCEDIMENTO', procedimento__isnull=True)
        | Q(tipo='PROCEDIMENTO', procedimento_id=procedimento_id)
    )


def vale_para(versao, procedimento) -> bool:
    """Mesma regra de q_termos_aplicaveis, sem query (listas ja carregadas, ex.: o wizard)."""
    if versao.tipo == 'LGPD':
        return versao.procedimento_id is None
    return versao.tipo == 'PROCEDIMENTO' and versao.procedimento_id in (None, getattr(procedimento, 'pk', procedimento))


def termos_aplicaveis(procedimento):
    """Versoes ATIVAS que valem para o procedimento (LGPD primeiro)."""
    pid = getattr(procedimento, 'pk', procedimento)
    return VersaoTermo.objects.filter(q_termos_aplicaveis(pid), ativa=True).order_by('tipo', 'pk')


def termos_pendentes(cliente, procedimento, *, so_procedimento=False):
    """Versoes aplicaveis que o cliente ainda nao aceitou."""
    assinadas = AceiteTermo.objects.filter(cliente=cliente).values('versao_termo_id')
    qs = termos_aplicaveis(procedimento).exclude(pk__in=assinadas)
    if so_procedimento:
        qs = qs.filter(tipo='PROCEDIMENTO')
    return list(qs)


def aceita_assinatura(atendimento) -> bool:
    """Link do termo vale ate o FIM do atendimento (nao 7 dias do envio).

    Cancelado/realizado/faltou/reagendado ou ja encerrado nao aceita mais:
    aceite de termo de procedimento depois do procedimento nao prova nada.
    """
    return (
        atendimento.status in STATUS_ACEITA_TERMO
        and timezone.now() < atendimento.data_hora_fim
    )


def ids_com_termo_procedimento_pendente(atendimentos) -> set[int]:
    """Ids dos atendimentos ativos com termo de PROCEDIMENTO ainda nao aceito.

    Em lote (3 queries no maximo) p/ listas do painel/portal. So o termo do
    procedimento conta aqui: a LGPD pendente e acompanhada no compliance e
    nao deve travar/poluir a agenda de quem executa.
    """
    ativos = [a for a in atendimentos if a.status in STATUS_ACEITA_TERMO]
    if not ativos:
        return set()
    proc_ids = {a.procedimento_id for a in ativos}
    versoes = list(
        VersaoTermo.objects.filter(ativa=True, tipo='PROCEDIMENTO')
        .filter(Q(procedimento__isnull=True) | Q(procedimento_id__in=proc_ids))
        .values_list('pk', 'procedimento_id')
    )
    if not versoes:
        return set()
    aceites = set(
        AceiteTermo.objects.filter(
            cliente_id__in={a.cliente_id for a in ativos},
            versao_termo_id__in=[pk for pk, _ in versoes],
        ).values_list('cliente_id', 'versao_termo_id')
    )
    pendentes = set()
    for at in ativos:
        for versao_id, proc_id in versoes:
            if proc_id in (None, at.procedimento_id) and (at.cliente_id, versao_id) not in aceites:
                pendentes.add(at.pk)
                break
    return pendentes


def url_termo(token: str) -> str:
    return f"{settings.SITE_URL.rstrip('/')}{reverse('aranha:termo_assinatura', args=[token])}"


def obter_link_termo(atendimento, canal=Notificacao.Canal.WHATSAPP):
    """(notificacao, url, notificacao_criada) — emite um link NOVO do termo.

    O token so existe em claro na geracao (link_acesso guarda o hash), entao
    nao da p/ reexibir o link de antes: cada chamada emite outro (decisao D1,
    spec padrao-sql §7.1). Os anteriores continuam validos ate o fim do
    atendimento (nao revoga: o link que a recepcao ja mandou segue abrindo).
    A Notificacao TERMO do atendimento e reaproveitada (1 por atendimento).
    """
    from . import links

    notif = (
        Notificacao.objects.filter(Q_NOTIF_TERMO, atendimento=atendimento)
        .order_by('-criado_em', '-pk').first()
    )
    criada = notif is None
    if criada:
        if canal not in dict(Notificacao.CANAL_CHOICES):
            canal = 'WHATSAPP'
        notif = Notificacao.objects.create(
            atendimento=atendimento,
            tipo=TIPO_NOTIF_TERMO,
            canal=canal,
            status=Notificacao.Status.PENDENTE,
        )
    token = links.emitir(links.TERMO, notif, expira_em=atendimento.data_hora_fim)
    return notif, url_termo(token), criada
