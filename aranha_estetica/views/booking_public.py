"""Fluxo publico de agendamento — 3 steps (procedimento, data/horario, confirmar).

Identidade do booking = TELEFONE verificado por OTP (SMS) na sessao — exigido
de TODO agendamento (cliente novo ou recorrente). O servidor revalida o slot
(SlotService), procedimento/profissional ativos + habilitacao, regra de
bloqueio online e anamnese obrigatoria. Slot lock via cache + SELECT FOR
UPDATE + exclusion constraint (Postgres). E-mails best-effort (Celery).
"""
import json
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, IntegrityError, OperationalError, ProgrammingError, transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..models import (
    AceiteTermo,
    Atendimento,
    Cliente,
    Feriado,
    FormularioAnamnese,
    Notificacao,
    Procedimento,
    Profissional,
    RespostaAnamnese,
    VersaoTermo,
)
from ..services import otp as otp_service
from ..services.agendamento_service import formatar_brl, formatar_data_hora
from ..services.disponibilidade import profissional_habilitado, slot_disponivel
from ..utils.captcha import turnstile_enabled, turnstile_site_key
from ..utils.datas import hoje as hoje_local
from ..utils.pii import mask_email, mask_telefone
from ..utils.precos import preco_base_map, preco_para
from ..utils.security import client_ip as _client_ip
from ..utils.sms import sms_disponivel
from ..utils.whatsapp import gerar_token
from ..validators import validate_data_nascimento

logger = logging.getLogger(__name__)

SESSAO_REHIDRATAR = 'booking_rehidratar'
MSG_BLOQUEADO_ONLINE = (
    'Seu cadastro está com agendamento online suspenso. '
    'Fale conosco pelo WhatsApp para marcar seu horário.'
)


class _Recusa(Exception):
    """Recusa dentro do atomic: desfaz o que foi gravado e volta com a mensagem."""


def _voltar_com_erro(request, mensagem):
    """Erro no confirmar: mensagem + wizard reidrata o estado salvo no navegador."""
    messages.error(request, mensagem)
    request.session[SESSAO_REHIDRATAR] = True
    return redirect('aranha:agendamento_publico')


def _enfileirar_email(funcao, destinatario, dados):
    """E-mail best-effort: falha de notificacao nunca derruba o agendamento ja salvo."""
    try:
        from ..tasks import send_email_async
        send_email_async.delay(funcao, destinatario, dados)
    except Exception:  # noqa: BLE001 — notificacao nao pode virar 500 pos-commit
        logger.warning('booking_email_falhou', extra={'funcao': funcao}, exc_info=True)


def _eh_sobreposicao(exc) -> bool:
    """IntegrityError da exclusion constraint (23P01) = corrida de slot perdida."""
    causa = getattr(exc, '__cause__', None)
    return getattr(causa, 'pgcode', None) == '23P01' or 'excl_atendimento_sobreposicao' in str(exc)


def _formularios_aplicaveis(procedimento):
    """Anamneses pre-atendimento ativas que valem p/ o procedimento."""
    return FormularioAnamnese.objects.filter(
        Q(escopo='GLOBAL')
        | Q(escopo='CATEGORIA', categoria=procedimento.categoria)
        | Q(escopo='PROCEDIMENTO', procedimento=procedimento),
        tipo='ANAMNESE', ativo=True,
    )


def _resposta_preenchida(valor) -> bool:
    if isinstance(valor, list):
        return any(str(v).strip() for v in valor)
    return valor is not None and str(valor).strip() != ''


def _validar_anamnese(raw, procedimento):
    """Parse + validacao das respostas. Retorna (lista[(form, respostas)], erro|None).

    Ignora formularios fora do conjunto aplicavel; exige os obrigatorios e os
    campos obrigatorios deles.
    """
    try:
        dados = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        logger.warning('booking_anamnese_json_invalido')
        dados = {}
    if not isinstance(dados, dict):
        dados = {}

    salvar = []
    for form in _formularios_aplicaveis(procedimento):
        respostas = dados.get(str(form.pk))
        if not isinstance(respostas, dict):
            respostas = {}
        schema = form.schema_json if isinstance(form.schema_json, list) else []
        chaves = {c.get('key') for c in schema if isinstance(c, dict)}
        respostas = {k: v for k, v in respostas.items() if k in chaves}
        preenchido = any(_resposta_preenchida(v) for v in respostas.values())
        if form.obrigatorio or preenchido:
            faltando = [
                c.get('label') or c.get('key')
                for c in schema
                if isinstance(c, dict) and c.get('obrigatorio')
                and not _resposta_preenchida(respostas.get(c.get('key')))
            ]
            if faltando:
                return [], f'Responda o questionário "{form.nome}": {", ".join(faltando)}.'
        if preenchido:
            salvar.append((form, respostas))
    return salvar, None


@ratelimit(key='ip', rate='300/h', method='GET', block=True)
def agendamento_publico(request):
    """Pagina publica de agendamento (wizard)."""
    procedimentos_com_preco = []
    try:
        procedimentos = list(Procedimento.objects.filter(ativo=True))
        precos = preco_base_map(procedimentos)
        for proc in procedimentos:
            valor = precos.get(proc.pk)
            procedimentos_com_preco.append({
                'id': proc.pk,
                'nome': proc.nome,
                'descricao': proc.descricao or '',
                'duracao_minutos': proc.duracao_minutos,
                'preco': float(valor) if valor is not None else 0,
                'categoria': proc.categoria,
                'categoria_label': proc.get_categoria_display(),
            })
    except (OperationalError, ProgrammingError):
        logger.warning('booking_tabelas_procedimento_indisponiveis')

    ids_validos = {str(p['id']) for p in procedimentos_com_preco}
    proc_preselect = request.GET.get('procedimento', '')
    if proc_preselect not in ids_validos:
        proc_preselect = ''

    prof_preselect = request.GET.get('profissional', '')
    if not (prof_preselect.isdigit()
            and Profissional.objects.filter(pk=prof_preselect, ativo=True).exists()):
        prof_preselect = ''

    categorias_disponiveis = sorted({
        (p['categoria'], p['categoria_label']) for p in procedimentos_com_preco
    })

    formularios_anamnese = []
    try:
        formularios_anamnese = [
            {
                'id': row['id'],
                'nome': row['nome'],
                'escopo': row['escopo'],
                'categoria': row['categoria'],
                'procedimento_id': row['procedimento_id'],
                'obrigatorio': row['obrigatorio'],
                'schema': row['schema_json'],
            }
            for row in FormularioAnamnese.objects.filter(ativo=True, tipo='ANAMNESE').values(
                'id', 'nome', 'escopo', 'categoria',
                'procedimento_id', 'obrigatorio', 'schema_json',
            )
        ]
    except (OperationalError, ProgrammingError):
        pass

    context = {
        'procedimentos': procedimentos_com_preco,
        'categorias_disponiveis': categorias_disponiveis,
        'formularios_anamnese_data': formularios_anamnese,
        'proc_preselect': proc_preselect,
        'prof_preselect': prof_preselect,
        # Telefone ja verificado nesta sessao (reidratar sem exigir novo SMS)
        'otp_telefone': otp_service.telefone_verificado_agendamento(request),
        'rehidratar': bool(request.session.pop(SESSAO_REHIDRATAR, False)),
        # Sem canal de SMS (prod sem provedor) o OTP e impossivel: avisa ja no passo 3
        'sms_disponivel': sms_disponivel(),
        'turnstile_site_key': turnstile_site_key(),
        'turnstile_enabled': turnstile_enabled(),
    }

    return render(request, 'agenda/agendamento_publico.html', context)


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def confirmar_agendamento(request):
    """Processa confirmacao SEM login (telefone verificado por OTP na sessao)."""
    if request.method != 'POST':
        return redirect('aranha:agendamento_publico')

    if request.POST.get('website', '').strip():
        logger.info('booking_honeypot_triggered', extra={'ip': _client_ip(request)})
        return redirect('aranha:agendamento_publico')

    nome = (request.POST.get('nome') or '').strip()[:150]
    telefone_raw = (request.POST.get('telefone') or '').strip()
    data_nascimento_str = (request.POST.get('data_nascimento') or '').strip()
    email = (request.POST.get('email') or '').strip().lower() or None
    procedimento_id = str(request.POST.get('procedimento') or '').strip()
    profissional_id = str(request.POST.get('profissional') or '').strip()
    datetime_str = (request.POST.get('datetime') or '').strip()
    consent_email_marketing = request.POST.get('consent_email_marketing') == 'on'
    consent_whatsapp_nps = request.POST.get('consent_whatsapp_nps') == 'on'
    consent_whatsapp_confirmacao = request.POST.get('consent_whatsapp_confirmacao') == 'on'

    if not all([nome, telefone_raw, data_nascimento_str, procedimento_id, profissional_id, datetime_str]):
        return _voltar_com_erro(request, 'Preencha todos os campos obrigatórios.')

    telefone = otp_service.normalizar_telefone_br(telefone_raw)
    if not telefone:
        return _voltar_com_erro(request, 'Informe um celular válido com DDD, ex.: (17) 99999-9999.')

    # Gate unico: o telefone do agendamento precisa ser o verificado por SMS
    # nesta sessao (vale p/ cliente novo e recorrente; e-mail nunca e identidade).
    if otp_service.telefone_verificado_agendamento(request) != telefone:
        return _voltar_com_erro(
            request, 'Confirme seu celular com o código enviado por SMS antes de concluir.'
        )

    if email:
        try:
            validate_email(email)
        except ValidationError:
            return _voltar_com_erro(request, 'Informe um e-mail válido ou deixe o campo em branco.')

    try:
        data_nascimento = datetime.strptime(data_nascimento_str, '%Y-%m-%d').date()
        validate_data_nascimento(data_nascimento)
    except (ValueError, ValidationError):
        return _voltar_com_erro(request, 'Data de nascimento inválida.')

    hoje = hoje_local()
    idade = hoje.year - data_nascimento.year - (
        (hoje.month, hoje.day) < (data_nascimento.month, data_nascimento.day)
    )
    if idade < 18:
        return _voltar_com_erro(request, 'É necessário ter pelo menos 18 anos para agendar.')

    if not (procedimento_id.isdigit() and profissional_id.isdigit()):
        return _voltar_com_erro(request, 'Dados do agendamento inválidos. Refaça a seleção.')
    try:
        procedimento = Procedimento.objects.get(pk=int(procedimento_id), ativo=True)
        profissional = Profissional.objects.get(pk=int(profissional_id), ativo=True)
        data_hora = datetime.fromisoformat(datetime_str)
        if timezone.is_naive(data_hora):
            data_hora = timezone.make_aware(data_hora)
        data_hora = timezone.localtime(data_hora)
    except (Procedimento.DoesNotExist, Profissional.DoesNotExist, ValueError, TypeError, OverflowError):
        return _voltar_com_erro(request, 'Dados do agendamento inválidos. Refaça a seleção.')

    if not profissional_habilitado(profissional, procedimento):
        return _voltar_com_erro(request, 'Este profissional não realiza o procedimento escolhido.')

    if data_hora <= timezone.now():
        return _voltar_com_erro(request, 'Escolha uma data e horário futuros.')

    if Feriado.objects.filter(data=data_hora.date(), bloqueia_agendamento=True).exists():
        return _voltar_com_erro(request, 'Esta data é feriado/recesso — escolha outro dia.')

    # Slot precisa ser um dos oferecidos (expediente, folga, bloqueio, buffer,
    # antecedencia minima/maxima, sobreposicao com a duracao inteira).
    if not slot_disponivel(profissional, procedimento, data_hora):
        return _voltar_com_erro(request, 'Este horário não está mais disponível. Escolha outro.')

    anamneses, erro_anamnese = _validar_anamnese(
        (request.POST.get('anamnese_respostas') or '').strip(), procedimento,
    )
    if erro_anamnese:
        return _voltar_com_erro(request, erro_anamnese)

    # E-mail de outro cadastro nao e reaproveitado (evita sequestro + IntegrityError)
    if email and Cliente.objects.filter(email__iexact=email).exclude(telefone=telefone).exists():
        return _voltar_com_erro(
            request,
            'Este e-mail já está vinculado a outro cadastro. Use outro e-mail ou deixe em branco.',
        )

    data_hora_fim = data_hora + timedelta(minutes=procedimento.duracao_minutos)
    slot_key = f'booking_slot:{profissional.pk}:{data_hora.isoformat()}'
    if not cache.add(slot_key, '1', timeout=30):
        return _voltar_com_erro(
            request, 'Este horário está sendo confirmado por outra pessoa. Tente outro.'
        )

    try:
        with transaction.atomic():
            agora = timezone.now()
            ip_origem = _client_ip(request) or None
            defaults = {
                'nome': nome,
                'data_nascimento': data_nascimento,
                'email': email,
                'ativo': True,
            }
            if consent_email_marketing:
                defaults.update({
                    'consent_email_marketing': True,
                    'consent_email_marketing_em': agora,
                    'consent_email_marketing_ip': ip_origem,
                })
            if consent_whatsapp_nps:
                defaults.update({
                    'consent_whatsapp_nps': True,
                    'consent_whatsapp_nps_em': agora,
                    'consent_whatsapp_nps_ip': ip_origem,
                })
            if consent_whatsapp_confirmacao:
                defaults.update({
                    'consent_whatsapp_confirmacao': True,
                    'consent_whatsapp_confirmacao_em': agora,
                    'consent_whatsapp_confirmacao_ip': ip_origem,
                })

            cliente, created = Cliente.objects.select_for_update().get_or_create(
                telefone=telefone,
                defaults=defaults,
            )
            if not created and (cliente.bloqueado_online or not cliente.ativo):
                # Regra das 3 faltas / bloqueio manual do painel.
                raise _Recusa(MSG_BLOQUEADO_ONLINE)
            if not created:
                atualizar = False
                if cliente.nome != nome:
                    cliente.nome = nome
                    atualizar = True
                if not cliente.data_nascimento and data_nascimento:
                    cliente.data_nascimento = data_nascimento
                    atualizar = True
                if not cliente.email and email:
                    cliente.email = email
                    atualizar = True
                if consent_email_marketing and not cliente.consent_email_marketing:
                    cliente.consent_email_marketing = True
                    cliente.consent_email_marketing_em = agora
                    cliente.consent_email_marketing_ip = ip_origem
                    atualizar = True
                if consent_whatsapp_nps and not cliente.consent_whatsapp_nps:
                    cliente.consent_whatsapp_nps = True
                    cliente.consent_whatsapp_nps_em = agora
                    cliente.consent_whatsapp_nps_ip = ip_origem
                    atualizar = True
                if consent_whatsapp_confirmacao and not cliente.consent_whatsapp_confirmacao:
                    cliente.consent_whatsapp_confirmacao = True
                    cliente.consent_whatsapp_confirmacao_em = agora
                    cliente.consent_whatsapp_confirmacao_ip = ip_origem
                    atualizar = True
                if atualizar:
                    cliente.save()

            conflito = Atendimento.objects.select_for_update().filter(
                profissional=profissional,
                data_hora_inicio__lt=data_hora_fim,
                data_hora_fim__gt=data_hora,
                status__in=Atendimento.STATUS_ATIVOS,
            ).exists()
            if conflito:
                raise _Recusa('Este horário já foi reservado. Por favor, escolha outro.')

            preco_obj = preco_para(procedimento, profissional)
            valor = preco_obj.valor if preco_obj else None

            atendimento = Atendimento.objects.create(
                cliente=cliente,
                profissional=profissional,
                procedimento=procedimento,
                data_hora_inicio=data_hora,
                data_hora_fim=data_hora_fim,
                valor_cobrado=valor,
                status=Atendimento.STATUS_PENDENTE,
            )

            for form, respostas in anamneses:
                RespostaAnamnese.objects.create(
                    formulario=form, cliente=cliente,
                    atendimento=atendimento, respostas_json=respostas,
                )

            termos_pendentes = VersaoTermo.objects.filter(
                Q(tipo='LGPD') | Q(procedimento=procedimento),
                ativa=True,
            )
            assinados_ids = set(
                AceiteTermo.objects.filter(cliente=cliente).values_list('versao_termo_id', flat=True)
            )
            tem_pendente = any(t.pk not in assinados_ids for t in termos_pendentes)
            dados_termo = None
            email_cliente = cliente.email
            if tem_pendente and email_cliente:
                # Token de termo: canal EMAIL — nao vale em /confirmar/<token>/.
                token_termo = gerar_token()
                Notificacao.objects.create(
                    atendimento=atendimento,
                    tipo='LEMBRETE',
                    canal='EMAIL',
                    status='PENDENTE',
                    token=token_termo,
                )
                dados_termo = {
                    'nome': nome,
                    'link_termo': f"{settings.SITE_URL}{reverse('aranha:termo_assinatura', args=[token_termo])}",
                }
    except _Recusa as recusa:
        return _voltar_com_erro(request, str(recusa))
    except IntegrityError as exc:
        if _eh_sobreposicao(exc):
            # Outro request reservou janela sobreposta entre o check e o INSERT.
            logger.info('booking_slot_corrida_perdida', extra={'telefone': mask_telefone(telefone)})
            return _voltar_com_erro(request, 'Este horário acabou de ser reservado. Por favor, escolha outro.')
        logger.error(
            'booking_confirmar_falha',
            extra={
                'erro': str(exc),
                'email': mask_email(email) if email else None,
                'telefone': mask_telefone(telefone),
            },
            exc_info=True,
        )
        return _voltar_com_erro(request, 'Ocorreu um erro ao confirmar o agendamento. Tente novamente.')
    except DatabaseError as exc:
        logger.error(
            'booking_confirmar_falha',
            extra={
                'erro': str(exc),
                'email': mask_email(email) if email else None,
                'telefone': mask_telefone(telefone),
            },
            exc_info=True,
        )
        return _voltar_com_erro(request, 'Ocorreu um erro ao confirmar o agendamento. Tente novamente.')
    finally:
        # Lock so serializa confirmacoes simultaneas; depois o banco decide.
        cache.delete(slot_key)

    data_formatada = formatar_data_hora(data_hora)

    if dados_termo:
        _enfileirar_email('enviar_termos_pendentes_email', email_cliente, dados_termo)

    usuario_prof = getattr(profissional, 'usuario', None)
    prof_email = getattr(usuario_prof, 'email', None)
    if prof_email:
        # Aprovar/rejeitar e POST dentro do portal: o e-mail so leva ate a agenda
        # do dia (GET). link_aprovar/link_rejeitar = mesmo destino (compat template).
        link_revisar = (
            f"{settings.SITE_URL}{reverse('aranha:profissional_agenda')}"
            f"?data={data_hora.strftime('%Y-%m-%d')}"
        )
        _enfileirar_email('enviar_aprovacao_profissional_email', prof_email, {
            'profissional': profissional.nome,
            'cliente': nome,
            'procedimento': procedimento.nome,
            'data_hora': data_formatada,
            'link_revisar': link_revisar,
            'link_aprovar': link_revisar,
            'link_rejeitar': link_revisar,
        })

    request.session['agendamento_sucesso'] = {
        'nome': nome,
        'procedimento': procedimento.nome,
        'profissional': profissional.nome,
        'data_hora': data_formatada,
        'valor': formatar_brl(valor) if valor else 'A consultar',
        'pendente': True,
        'email': bool(email_cliente),
    }
    otp_service.limpar_verificacao_agendamento(request)
    request.session.pop(SESSAO_REHIDRATAR, None)

    return redirect('aranha:agendamento_sucesso')


def agendamento_sucesso(request):
    """Pagina de sucesso apos agendamento/reagendamento."""
    dados = request.session.pop('agendamento_sucesso', None)
    if not dados:
        return redirect('aranha:agendamento_publico')
    return render(request, 'agenda/agendamento_sucesso.html', {'dados': dados})
