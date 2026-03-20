import json
import logging
from datetime import datetime, timedelta

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..models import (
    Procedimento, Profissional, Cliente, Atendimento,
    Preco, DisponibilidadeProfissional, Promocao
)

logger = logging.getLogger(__name__)

# WhatsApp Bot — Webhook API
# Integração com APIs de WhatsApp (Twilio, Meta Business, etc.)
# Este módulo processa mensagens recebidas e gera respostas automáticas.

MENU_PRINCIPAL = (
    "🧘 *Shiva Zen — Clínica de Estética*\n\n"
    "Olá! Como posso ajudar?\n\n"
    "1️⃣ Ver procedimentos disponíveis\n"
    "2️⃣ Ver promoções ativas\n"
    "3️⃣ Consultar horários disponíveis\n"
    "4️⃣ Verificar meu agendamento\n"
    "5️⃣ Falar com atendente\n\n"
    "Envie o número da opção desejada."
)


def _listar_procedimentos():
    """Retorna lista formatada de procedimentos."""
    procedimentos = Procedimento.objects.filter(ativo=True)
    linhas = ["📋 *Nossos Procedimentos:*\n"]

    faciais = procedimentos.filter(nome__in=[
        'Limpeza de Pele Profunda', 'Preenchimento Facial', 'Harmonização Facial',
        'Bioestimulador de Colágeno', 'Toxina Botulínica (Botox)', 'Peeling Químico',
        'Fototerapia LED', 'Microagulhamento', 'Skinbooster', 'Laser Fracionado'
    ])
    corporais = procedimentos.exclude(pk__in=faciais.values_list('pk', flat=True))

    if faciais.exists():
        linhas.append("\n✨ *Faciais:*")
        for proc in faciais:
            preco = Preco.objects.filter(procedimento=proc, profissional__isnull=True).first()
            valor = f"R$ {preco.valor:,.2f}".replace(',', '.') if preco else "Consultar"
            linhas.append(f"  • {proc.nome} — {valor}")

    if corporais.exists():
        linhas.append("\n💆 *Corporais:*")
        for proc in corporais:
            preco = Preco.objects.filter(procedimento=proc, profissional__isnull=True).first()
            valor = f"R$ {preco.valor:,.2f}".replace(',', '.') if preco else "Consultar"
            linhas.append(f"  • {proc.nome} — {valor}")

    linhas.append("\n📲 Para agendar, acesse: shivazen.com/agendamento")
    linhas.append("\nEnvie *0* para voltar ao menu.")
    return "\n".join(linhas)


def _listar_promocoes():
    """Retorna promoções ativas formatadas."""
    hoje = timezone.now().date()
    promos = Promocao.objects.filter(
        ativa=True, data_inicio__lte=hoje, data_fim__gte=hoje
    ).select_related('procedimento')

    if not promos.exists():
        return "😔 Nenhuma promoção ativa no momento.\n\nEnvie *0* para voltar ao menu."

    linhas = ["🏷️ *Promoções Ativas:*\n"]
    for promo in promos:
        linhas.append(f"🔥 *{promo.nome}*")
        linhas.append(f"   {promo.descricao}")
        if promo.desconto_percentual:
            linhas.append(f"   💰 {int(promo.desconto_percentual)}% OFF")
        if promo.preco_promocional:
            linhas.append(f"   💵 Por apenas R$ {promo.preco_promocional:,.2f}".replace(',', '.'))
        linhas.append(f"   📅 Até {promo.data_fim.strftime('%d/%m/%Y')}")
        linhas.append("")

    linhas.append("📲 Para agendar, acesse: shivazen.com/agendamento")
    linhas.append("\nEnvie *0* para voltar ao menu.")
    return "\n".join(linhas)


def _consultar_agendamento(telefone):
    """Busca agendamentos de um telefone."""
    clientes = Cliente.objects.filter(telefone__icontains=telefone)
    if not clientes.exists():
        return "❌ Nenhum agendamento encontrado com esse telefone.\n\nEnvie *0* para voltar ao menu."

    agendamentos = Atendimento.objects.filter(
        cliente__in=clientes,
        data_hora_inicio__gte=timezone.now(),
        status_atendimento__in=['AGENDADO', 'CONFIRMADO']
    ).select_related('profissional', 'procedimento').order_by('data_hora_inicio')[:5]

    if not agendamentos.exists():
        return "📋 Você não tem agendamentos futuros.\n\n📲 Para agendar: shivazen.com/agendamento\n\nEnvie *0* para voltar ao menu."

    linhas = ["📅 *Seus próximos agendamentos:*\n"]
    for ag in agendamentos:
        status_emoji = "✅" if ag.status_atendimento == 'CONFIRMADO' else "🕐"
        linhas.append(f"{status_emoji} *{ag.procedimento.nome}*")
        linhas.append(f"   📅 {ag.data_hora_inicio.strftime('%d/%m/%Y às %H:%M')}")
        linhas.append(f"   👩‍⚕️ {ag.profissional.nome}")
        linhas.append(f"   Status: {ag.status_atendimento}")
        linhas.append("")

    linhas.append("\nEnvie *0* para voltar ao menu.")
    return "\n".join(linhas)


def processar_mensagem(telefone, mensagem):
    """
    Processa uma mensagem recebida do WhatsApp e retorna a resposta.
    Stateless — baseado apenas no conteúdo da mensagem.
    """
    msg = mensagem.strip().lower()

    # Menu principal
    if msg in ('oi', 'olá', 'ola', 'menu', 'inicio', 'início', 'hi', 'hello', '0'):
        return MENU_PRINCIPAL

    # Opção 1 — Procedimentos
    if msg == '1' or 'procedimento' in msg or 'serviço' in msg or 'servico' in msg:
        return _listar_procedimentos()

    # Opção 2 — Promoções
    if msg == '2' or 'promoção' in msg or 'promocao' in msg or 'desconto' in msg:
        return _listar_promocoes()

    # Opção 3 — Horários
    if msg == '3' or 'horário' in msg or 'horario' in msg or 'agendar' in msg:
        return (
            "📅 *Consultar Horários*\n\n"
            "Para verificar horários disponíveis e agendar, acesse:\n"
            "🔗 shivazen.com/agendamento\n\n"
            "Lá você pode:\n"
            "  ✅ Escolher o procedimento\n"
            "  ✅ Ver datas e horários disponíveis\n"
            "  ✅ Confirmar seu agendamento\n\n"
            "Envie *0* para voltar ao menu."
        )

    # Opção 4 — Consultar agendamento
    if msg == '4' or 'meu agendamento' in msg or 'consultar' in msg:
        if telefone:
            return _consultar_agendamento(telefone)
        return "📱 Informe seu telefone para consultar agendamentos.\n\nEnvie *0* para voltar ao menu."

    # Opção 5 — Atendente
    if msg == '5' or 'atendente' in msg or 'humano' in msg or 'pessoa' in msg:
        return (
            "👋 *Transferindo para atendimento humano*\n\n"
            "Um membro da nossa equipe responderá em breve.\n"
            "Nosso horário de atendimento:\n"
            "📅 Seg-Sex: 9h às 18h\n"
            "📅 Sáb: 9h às 14h\n\n"
            "Obrigado pela paciência! 💛"
        )

    # Fallback
    return (
        "🤔 Não entendi sua mensagem.\n\n"
        "Envie *0* para ver o menu principal ou escolha:\n"
        "1️⃣ Procedimentos\n"
        "2️⃣ Promoções\n"
        "3️⃣ Horários\n"
        "4️⃣ Meu agendamento\n"
        "5️⃣ Falar com atendente"
    )


@csrf_exempt
@require_http_methods(["POST"])
@ratelimit(key='ip', rate='60/m', method='POST', block=True)
def whatsapp_webhook(request):
    """
    Webhook para receber mensagens do WhatsApp.
    Compatível com Twilio, Meta Business API, e similares.

    Espera JSON com:
    {
        "from": "5517999990001",
        "body": "texto da mensagem"
    }

    Retorna JSON com:
    {
        "reply": "texto da resposta"
    }
    """
    try:
        data = json.loads(request.body)
        telefone = data.get('from', data.get('From', '')).strip()
        mensagem = data.get('body', data.get('Body', '')).strip()

        if not mensagem:
            return JsonResponse({'error': 'Mensagem vazia'}, status=400)

        # Limpar telefone
        telefone_limpo = ''.join(filter(str.isdigit, telefone))

        resposta = processar_mensagem(telefone_limpo, mensagem)

        logger.info(f'WhatsApp bot: mensagem de ***{telefone_limpo[-4:] if len(telefone_limpo) > 4 else "????"}')

        return JsonResponse({
            'reply': resposta,
            'status': 'ok'
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    except Exception as e:
        logger.error(f'Erro no webhook WhatsApp: {e}', exc_info=True)
        return JsonResponse({'error': 'Erro interno'}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def whatsapp_webhook_verify(request):
    """
    Verificação de webhook (handshake) — usado pela Meta Business API.
    GET com hub.mode=subscribe, hub.verify_token, hub.challenge.
    """
    mode = request.GET.get('hub.mode', '')
    token = request.GET.get('hub.verify_token', '')
    challenge = request.GET.get('hub.challenge', '')

    # Token de verificação configurável
    VERIFY_TOKEN = 'shivazen_whatsapp_verify_2024'

    if mode == 'subscribe' and token == VERIFY_TOKEN:
        return JsonResponse(int(challenge) if challenge.isdigit() else challenge, safe=False)

    return JsonResponse({'error': 'Verificação falhou'}, status=403)
