"""NPS: o calculo do painel, quem recebe a pesquisa, o registro da nota e o alerta de detrator.

Calculo (fonte unica da visao geral e da tela de NPS): NPS = %promotores (9-10) - %detratores
(0-6), inteiro arredondado "meio p/ cima" (Decimal ROUND_HALF_UP: empate se afasta do zero) — o
mesmo do ``round(numeric)`` do Postgres. O ``round()`` do Python e bancario (``round(12.5) == 12``)
e divergia do espelho SQL em 1 ponto nos casos .5 (decisao D7, docs/specs/padrao-sql.md §5.4 item
3 / §7.1). Espelho SQL (paridade em tests/test_pg_paridade.py): ``fn_nps_periodo`` / ``vw_nps_mensal``.

Envio (D20): no maximo 1 pesquisa por cliente a cada NPS_INTERVALO_DIAS, nunca em retorno
(gratuito: distorce a media e cansa quem faz pacote), so por WhatsApp com
PESQUISA_SATISFACAO/WHATSAPP vigente, entre 24 h e NPS_JANELA_DIAS depois do atendimento e ate
NPS_MAX_FALHAS tentativas. A pesquisa do atendimento e unica (Notificacao NPS PENDENTE/ENVIADO).

Resposta: pela pagina do link (views/publico/nps) ou respondendo a nota no WhatsApp
(views/infra/webhooks) — as duas portas passam por ``registrar_avaliacao`` (1 avaliacao por
atendimento; clique duplo devolve a que ja existe).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils import timezone

from ..constants import NPS_INTERVALO_DIAS
from ..models import Atendimento, AvaliacaoNPS, Configuracao, Notificacao
from ..models.agendamentos import StatusAtendimento
from ..utils import email as canal_email
from ..utils import whatsapp as canal_whatsapp
from ..utils.branding import get_branding
from ..utils.datas import fmt_local
from . import comunicacao, consentimentos, links

logger = logging.getLogger(__name__)

UM = Decimal('1')
UM_DECIMO = Decimal('0.1')

# So atendimentos recentes (a 1a execucao nao dispara p/ o historico inteiro) e no maximo N
# tentativas por atendimento.
NPS_JANELA_DIAS = 7
NPS_MAX_FALHAS = 3
NOTA_MAXIMA_DETRATOR = 6
TAMANHO_MAXIMO_COMENTARIO = 2000


# ─── Calculo ──────────────────────────────────────────────────────────────

def calcular_nps(promotores, detratores, total) -> int | None:
    """%promotores - %detratores (inteiro, meio p/ cima); None sem respostas."""
    if not total:
        return None
    valor = Decimal((promotores or 0) - (detratores or 0)) * 100 / Decimal(total)
    return int(valor.quantize(UM, rounding=ROUND_HALF_UP))


def media_nota(soma, total) -> Decimal | None:
    """Media das notas com 1 casa (meio p/ cima) = round(avg(nota), 1) do Postgres."""
    if not total:
        return None
    return (Decimal(soma or 0) / Decimal(total)).quantize(UM_DECIMO, rounding=ROUND_HALF_UP)


def resumo_nps(qs) -> dict:
    """Agrega um queryset de AvaliacaoNPS (1 query) = colunas do fn_nps_periodo.

    {'qtd_respostas', 'qtd_promotores', 'qtd_neutros', 'qtd_detratores', 'nps', 'nota_media'}
    """
    agg = qs.aggregate(
        total=Count('id'),
        soma=Sum('nota'),
        promotores=Count('id', filter=Q(nota__gte=9)),
        neutros=Count('id', filter=Q(nota__gte=7, nota__lte=8)),
        detratores=Count('id', filter=Q(nota__lte=NOTA_MAXIMA_DETRATOR)),
    )
    total = agg['total'] or 0
    return {
        'qtd_respostas': total,
        'qtd_promotores': agg['promotores'] or 0,
        'qtd_neutros': agg['neutros'] or 0,
        'qtd_detratores': agg['detratores'] or 0,
        'nps': calcular_nps(agg['promotores'], agg['detratores'], total),
        'nota_media': media_nota(agg['soma'], total),
    }


# ─── Envio da pesquisa ────────────────────────────────────────────────────

def candidatos(agora=None):
    """Atendimentos que podem receber a pesquisa agora (mais recente primeiro).

    - REALIZADO, nao retorno, terminado ha 24 h+ e ha no maximo NPS_JANELA_DIAS, sem avaliacao;
    - cliente com PESQUISA_SATISFACAO/WHATSAPP vigente (ultimo registro do historico — descadastro
      ou 'PARAR' revogam, reconsentimento volta a valer);
    - sem NPS PENDENTE/ENVIADO do proprio atendimento (Exists correlacionado: um exclude() com duas
      condicoes na FK reversa virava dois EXISTS independentes e bloqueava o retry de quem tinha
      NPS FALHOU + LEMBRETE ENVIADO);
    - sem NPS ENVIADO a mesma cliente nos ultimos NPS_INTERVALO_DIAS;
    - menos de NPS_MAX_FALHAS tentativas falhas.
    Dois atendimentos da mesma cliente na mesma rodada: ``enviar_pesquisas`` pega so o 1o.
    """
    agora = agora or timezone.now()
    nps_do_atendimento = Notificacao.objects.filter(
        atendimento=OuterRef('pk'), tipo=Notificacao.Tipo.NPS,
        status__in=[Notificacao.Status.PENDENTE, Notificacao.Status.ENVIADO],
    )
    nps_recente_da_cliente = Notificacao.objects.filter(
        atendimento__cliente_id=OuterRef('cliente_id'), tipo=Notificacao.Tipo.NPS,
        status=Notificacao.Status.ENVIADO, criado_em__gte=agora - timedelta(days=NPS_INTERVALO_DIAS),
    )
    elegiveis = Atendimento.objects.filter(
        status=StatusAtendimento.REALIZADO,
        eh_retorno=False,
        data_hora_fim__lte=agora - timedelta(days=1),
        data_hora_fim__gte=agora - timedelta(days=NPS_JANELA_DIAS),
        avaliacaonps__isnull=True,
    )
    return (
        consentimentos.com_consentimento(elegiveis, *consentimentos.PESQUISA_WHATSAPP)
        .exclude(Exists(nps_do_atendimento))
        .exclude(Exists(nps_recente_da_cliente))
        .annotate(nps_falhas=Count(
            'notificacao',
            filter=Q(notificacao__tipo=Notificacao.Tipo.NPS, notificacao__status=Notificacao.Status.FALHOU),
        ))
        .filter(nps_falhas__lt=NPS_MAX_FALHAS)
        .select_related('cliente', 'procedimento')
        .order_by('-data_hora_fim', '-pk')
    )


def enviar_pesquisas() -> int:
    """Job nps_24h: envia a pesquisa aos candidatos. Devolve quantas sairam.

    Sem canal (fora de DEBUG e sem credencial) nao cria nada — nem FALHOU: quando o WhatsApp for
    configurado, a janela ainda pega os recentes. Falha de 1 cliente vira FALHOU (re-tentavel) e o
    lote segue."""
    if not canal_whatsapp.pode_enviar_whatsapp():
        logger.warning('nps_whatsapp_nao_configurado')
        return 0
    enviados, pesquisadas = 0, set()
    for at in candidatos():
        if at.cliente_id in pesquisadas:
            continue
        if not at.cliente.telefone:
            logger.warning('nps_cliente_sem_telefone', extra={'cliente_id': at.cliente_id})
            continue
        pesquisadas.add(at.cliente_id)
        notif = comunicacao.enviar_nps_whatsapp(at)
        enviados += notif.status == Notificacao.Status.ENVIADO
    logger.info('nps_enviados', extra={'enviados': enviados})
    return enviados


# ─── Resposta ─────────────────────────────────────────────────────────────

def registrar_avaliacao(atendimento, nota: int, *, comentario: str = '', autoriza_publicacao: bool = False,
                        link=None) -> tuple[AvaliacaoNPS, bool]:
    """Grava a nota do atendimento (1 por atendimento). -> (avaliacao, criada).

    Ja respondida: devolve a existente sem mudar nada (clique duplo, resposta pelo WhatsApp depois
    da web). ``link``: o LinkAcesso NPS usado — marcado como usado quando a avaliacao nasce."""
    if not 0 <= int(nota) <= 10:
        raise ValueError(f'nota fora da escala 0-10: {nota}')
    with transaction.atomic():
        avaliacao, criada = AvaliacaoNPS.objects.get_or_create(
            atendimento=atendimento,
            defaults={
                'nota': int(nota),
                'comentario': (comentario or '').strip()[:TAMANHO_MAXIMO_COMENTARIO],
                # opt-in explicito p/ virar depoimento publico (moderado no painel)
                'autoriza_publicacao': bool(autoriza_publicacao),
            },
        )
        if criada and link is not None:
            links.marcar_usado(link)
    if criada:
        logger.info('nps_registrado', extra={'atendimento_id': atendimento.pk, 'nota': avaliacao.nota})
    return avaliacao, criada


# ─── Alerta de detrator ───────────────────────────────────────────────────

def destino_alerta() -> str:
    """Quem recebe o alerta: regra email_admin (tela Regras operacionais; e-mail invalido gravado
    antes da validacao conta como vazio) > settings.ADMIN_EMAIL > e-mail de contato da clinica (marca)."""
    return str(Configuracao.ler('email_admin')) or settings.ADMIN_EMAIL or get_branding().get('CLINIC_EMAIL', '')


def _texto_alerta(avaliacao) -> tuple[str, str]:
    at = avaliacao.atendimento
    assunto = f"[{get_branding()['CLINIC_NAME']}] Alerta NPS — {at.cliente.nome} nota {avaliacao.nota}"
    corpo = (
        f'Cliente: {at.cliente.nome}\n'
        f'Procedimento: {at.procedimento.nome}\n'
        f'Profissional: {at.profissional.nome if at.profissional_id else "-"}\n'
        f'Data do atendimento: {fmt_local(at.data_hora_inicio)}\n'
        f'Nota: {avaliacao.nota}/10\n'
        f'Comentário: {avaliacao.comentario or "(sem comentário)"}\n'
    )
    return assunto, corpo


def alertar_detratores() -> int:
    """Job detrator_alerta: e-mail a clinica por nota <= 6 ainda nao avisada. Devolve quantos.

    Exatamente uma vez: a avaliacao e travada (skip_locked) e so vira alerta_enviado depois que o
    e-mail saiu; sem destino ou sem backend real nada e marcado (o alerta sai quando configurarem)."""
    pendentes = list(
        AvaliacaoNPS.objects.filter(nota__lte=NOTA_MAXIMA_DETRATOR, alerta_enviado=False)
        .order_by('criado_em').values_list('pk', flat=True)
    )
    if not pendentes:
        return 0
    destino = destino_alerta()
    if not destino:
        logger.warning('nps_detrator_sem_email_admin', extra={'pendentes': len(pendentes)})
        return 0
    if not canal_email.email_configurado():
        logger.warning('nps_detrator_email_nao_configurado', extra={'pendentes': len(pendentes)})
        return 0
    enviados = 0
    for pk in pendentes:
        with transaction.atomic():
            avaliacao = (
                AvaliacaoNPS.objects.select_for_update(skip_locked=True)
                .select_related('atendimento__cliente', 'atendimento__procedimento', 'atendimento__profissional')
                .filter(pk=pk, alerta_enviado=False).first()
            )
            if avaliacao is None:
                continue
            if canal_email.enviar_aviso_interno(destino, *_texto_alerta(avaliacao)):
                avaliacao.alerta_enviado = True
                avaliacao.save(update_fields=['alerta_enviado', 'atualizado_em'])
                enviados += 1
                logger.warning('nps_detrator_alerta_enviado',
                               extra={'cliente_id': avaliacao.atendimento.cliente_id, 'nota': avaliacao.nota})
    return enviados
