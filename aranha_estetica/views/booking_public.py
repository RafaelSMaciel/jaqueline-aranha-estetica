"""Fluxo publico de agendamento — 3 steps (procedimento, data/horario, confirmar).

Identidade do booking = TELEFONE verificado por OTP (SMS) na sessao — exigido
de TODO agendamento (cliente novo ou recorrente). O servidor revalida o slot
(SlotService), procedimento/profissional ativos + habilitacao, regra de
bloqueio online e anamnese obrigatoria. Slot lock via cache + SELECT FOR
UPDATE + exclusion constraint (Postgres). E-mails best-effort (Celery).

LGPD: aceite da Politica de Privacidade obrigatorio (grava AceiteTermo da
versao LGPD vigente); ficha de anamnese (dado de saude, art. 11) so com
consentimento especifico; termo(s) de procedimento aceitos no proprio wizard.
Preco gravado = preco com promocao NA DATA DO ATENDIMENTO (utils.precos).
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
    Procedimento,
    Profissional,
    Promocao,
    RespostaAnamnese,
    VersaoTermo,
)
from ..services import otp as otp_service
from ..services.agendamento_service import formatar_brl, formatar_data_hora
from ..services.anamnese import validar_respostas
from ..services.disponibilidade import profissional_habilitado, slot_disponivel
from ..utils.audit import registrar_log
from ..utils.captcha import turnstile_enabled, turnstile_site_key
from ..utils.datas import data_local
from ..utils.datas import hoje as hoje_local
from ..utils.email import email_configurado
from ..utils.parse import id_int
from ..utils.pii import mask_email, mask_telefone
from ..utils.precos import aplicar_promocao, preco_base_map, preco_com_promocao, promocao_vigente
from ..utils.security import client_ip as _client_ip
from ..utils.sms import sms_disponivel
from ..validators import validate_data_nascimento

logger = logging.getLogger(__name__)

SESSAO_REHIDRATAR = 'booking_rehidratar'
MSG_BLOQUEADO_ONLINE = (
    'Seu cadastro está com agendamento online suspenso. '
    'Fale conosco pelo WhatsApp para marcar seu horário.'
)
# Teto do JSON da anamnese (todos os formularios somados) — acima disso e abuso.
MAX_ANAMNESE_BYTES = 20_000
# Consentimento especifico p/ dado de saude (LGPD art. 11, I): o mesmo texto
# aparece no wizard e vai p/ a auditoria junto com data e IP.
TEXTO_CONSENTIMENTO_SAUDE = (
    'Autorizo a clínica a usar as informações de saúde que informei neste '
    'questionário (como alergias, gestação e medicamentos) somente para avaliar '
    'a segurança do procedimento e cuidar do meu atendimento, conforme a '
    'Política de Privacidade (LGPD, art. 11).'
)
# Consents de comunicacao (checkbox do wizard -> campos do Cliente).
CONSENTS_COMUNICACAO = (
    'consent_email_marketing',
    'consent_whatsapp_confirmacao',
    'consent_whatsapp_nps',
)


class _Recusa(Exception):
    """Recusa dentro do atomic: desfaz o que foi gravado e volta com a mensagem."""


def _voltar_com_erro(request, mensagem):
    """Erro no confirmar: mensagem + wizard reidrata o estado salvo no navegador."""
    messages.error(request, mensagem)
    request.session[SESSAO_REHIDRATAR] = True
    return redirect('aranha:agendamento_publico')


def link_revisar_agenda(data_hora):
    """Link (GET) p/ a agenda do portal no dia LOCAL do atendimento."""
    return (
        f"{settings.SITE_URL}{reverse('aranha:profissional_agenda')}"
        f"?data={data_local(data_hora):%Y-%m-%d}"
    )


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

    Ignora formularios fora do conjunto aplicavel; valida o conteudo contra o
    schema (services.anamnese.validar_respostas: so chaves do schema, tipo,
    opcoes, tamanho, bool normalizado) e exige os obrigatorios.
    """
    if len(raw) > MAX_ANAMNESE_BYTES:
        return [], 'As respostas do questionário ficaram longas demais. Resuma e tente novamente.'
    try:
        dados = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        logger.warning('booking_anamnese_json_invalido')
        dados = {}
    if not isinstance(dados, dict):
        dados = {}

    salvar = []
    for form in _formularios_aplicaveis(procedimento):
        brutas = dados.get(str(form.pk))
        if not isinstance(brutas, dict):
            brutas = {}
        schema = form.schema_json if isinstance(form.schema_json, list) else []
        chaves = {c.get('key') for c in schema if isinstance(c, dict)}
        preenchido = any(_resposta_preenchida(v) for k, v in brutas.items() if k in chaves)
        if not (form.obrigatorio or preenchido):
            continue
        respostas, erros = validar_respostas(schema, brutas)
        if erros:
            return [], f'Questionário "{form.nome}": {" ".join(erros[:3])}'
        if respostas:
            salvar.append((form, respostas))
    return salvar, None


def _termos_procedimento(procedimento):
    """Termos de procedimento ativos que valem p/ o procedimento (proprio + geral)."""
    return list(
        VersaoTermo.objects.filter(
            Q(procedimento=procedimento) | Q(procedimento__isnull=True),
            tipo='PROCEDIMENTO', ativa=True,
        ).order_by('pk')
    )


def _precos_card(procedimentos):
    """{pk: (valor_final_hoje, promocao|None, valor_cheio)} p/ o 'A partir de' do card.

    Mesma conta do agendamento (utils.precos): promocao vigente HOJE aplicada
    sobre o valor mostrado (preco base ou, sem ele, o menor do profissional —
    valor_base). O valor gravado usa a data do atendimento e o profissional.
    """
    cheios = preco_base_map(procedimentos)
    hoje = hoje_local()
    vigentes = Promocao.objects.filter(ativa=True, data_inicio__lte=hoje, data_fim__gte=hoje)
    ha_geral = vigentes.filter(procedimento__isnull=True).exists()
    com_promo = set(
        vigentes.filter(procedimento__in=procedimentos).values_list('procedimento_id', flat=True)
    )
    out = {}
    for proc in procedimentos:
        cheio = cheios.get(proc.pk)
        if cheio is None:
            continue
        promo = (
            promocao_vigente(proc, hoje, valor_base=cheio)
            if (ha_geral or proc.pk in com_promo) else None
        )
        final = aplicar_promocao(cheio, promo)
        if promo is not None and final >= cheio:
            promo, final = None, cheio
        out[proc.pk] = (final, promo, cheio)
    return out


@ratelimit(key='ip', rate='300/h', method='GET', block=True)
def agendamento_publico(request):
    """Pagina publica de agendamento (wizard)."""
    procedimentos_com_preco = []
    try:
        procedimentos = list(Procedimento.objects.filter(ativo=True))
        precos = _precos_card(procedimentos)
        for proc in procedimentos:
            final, promo, cheio = precos.get(proc.pk, (None, None, None))
            procedimentos_com_preco.append({
                'id': proc.pk,
                'nome': proc.nome,
                'descricao': proc.descricao or '',
                'duracao_minutos': proc.duracao_minutos,
                'preco': float(final) if final is not None else 0,
                # cheio sem promo: fallback do resumo (o valor real vem por data/profissional)
                'preco_base': float(cheio) if cheio is not None else 0,
                'preco_cheio': float(cheio) if promo is not None else None,
                'promocao': promo.nome if promo is not None else '',
                'promocao_ate': promo.data_fim if promo is not None else None,
                'categoria': proc.categoria,
                'categoria_label': proc.get_categoria_display(),
            })
    except (OperationalError, ProgrammingError):
        logger.warning('booking_tabelas_procedimento_indisponiveis')

    ids_validos = {str(p['id']) for p in procedimentos_com_preco}
    proc_preselect = request.GET.get('procedimento', '')
    if proc_preselect not in ids_validos:
        proc_preselect = ''

    prof_id = id_int(request.GET.get('profissional'))
    prof_preselect = ''
    if prof_id is not None and Profissional.objects.filter(pk=prof_id, ativo=True).exists():
        prof_preselect = str(prof_id)

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

    termo_lgpd = None
    termos_procedimento = []
    try:
        termo_lgpd = VersaoTermo.lgpd_vigente()
        termos_procedimento = [
            {
                'id': t.pk,
                'titulo': t.titulo,
                'versao': t.versao,
                'conteudo': t.conteudo,
                'procedimento_id': t.procedimento_id,
            }
            for t in VersaoTermo.objects.filter(tipo='PROCEDIMENTO', ativa=True).order_by('pk')
        ]
    except (OperationalError, ProgrammingError):
        pass

    context = {
        'procedimentos': procedimentos_com_preco,
        'categorias_disponiveis': categorias_disponiveis,
        'formularios_anamnese_data': formularios_anamnese,
        'termo_lgpd': termo_lgpd,
        'termos_procedimento_data': termos_procedimento,
        'texto_consentimento_saude': TEXTO_CONSENTIMENTO_SAUDE,
        'proc_preselect': proc_preselect,
        'prof_preselect': prof_preselect,
        # Telefone ja verificado nesta sessao (reidratar sem exigir novo SMS)
        'otp_telefone': otp_service.telefone_verificado_agendamento(request),
        'rehidratar': bool(request.session.pop(SESSAO_REHIDRATAR, False)),
        # Sem canal de SMS (prod sem provedor) o OTP e impossivel: avisa ja no
        # passo 1 (antes de escolher tratamento/horario) e de novo no passo 3
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
    consents = {campo: request.POST.get(campo) == 'on' for campo in CONSENTS_COMUNICACAO}
    # '1' = os checkboxes mostravam o estado atual do cadastro (prefill do OTP):
    # so entao desmarcar revoga. Sem isso o POST so concede (nunca revoga as cegas).
    consents_sincronizados = request.POST.get('consents_sincronizados') == '1'
    aceite_politica = request.POST.get('aceite_politica') == 'on'
    consent_dados_saude = request.POST.get('consent_dados_saude') == 'on'

    if not all([nome, telefone_raw, data_nascimento_str, procedimento_id, profissional_id, datetime_str]):
        return _voltar_com_erro(request, 'Preencha todos os campos obrigatórios.')

    if not aceite_politica:
        return _voltar_com_erro(
            request, 'Para agendar, confirme que leu e aceita a Política de Privacidade.'
        )

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

    proc_pk, prof_pk = id_int(procedimento_id), id_int(profissional_id)
    if proc_pk is None or prof_pk is None:
        return _voltar_com_erro(request, 'Dados do agendamento inválidos. Refaça a seleção.')
    try:
        procedimento = Procedimento.objects.get(pk=proc_pk, ativo=True)
        profissional = Profissional.objects.get(pk=prof_pk, ativo=True)
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
    if anamneses and not consent_dados_saude:
        # Dado de saude (LGPD art. 11): nada e gravado sem o consentimento destacado.
        return _voltar_com_erro(
            request,
            'Para enviar o questionário pré-atendimento, marque a autorização de uso '
            'das suas informações de saúde.',
        )

    # Termo(s) de procedimento: aceite no proprio wizard (o link por e-mail nao
    # chegava a quem nao informa e-mail). Versao ja aceita antes nao e exigida.
    termos_proc = _termos_procedimento(procedimento)
    ja_aceitos = set(
        AceiteTermo.objects.filter(
            cliente__telefone=telefone, versao_termo__in=termos_proc,
        ).values_list('versao_termo_id', flat=True)
    ) if termos_proc else set()
    termo_faltando = next(
        (t for t in termos_proc
         if t.pk not in ja_aceitos and request.POST.get(f'aceite_termo_{t.pk}') != 'on'),
        None,
    )
    if termo_faltando is not None:
        return _voltar_com_erro(
            request,
            f'Leia e aceite o termo "{termo_faltando.titulo}" para concluir o agendamento.',
        )

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
            for campo, marcado in consents.items():
                if marcado:
                    defaults.update({
                        campo: True,
                        f'{campo}_em': agora,
                        f'{campo}_ip': ip_origem,
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
                if email and cliente.email != email:
                    # Telefone acabou de ser provado por SMS: o e-mail informado
                    # pela dona do celular substitui o do cadastro (inclusive um
                    # plantado por terceiro sem verificacao). Em branco = mantem.
                    cliente.email = email
                    atualizar = True
                for campo, marcado in consents.items():
                    # Concede ao marcar; revoga ao desmarcar SO se o checkbox
                    # mostrava o estado do cadastro (data/IP = os da revogacao).
                    if marcado != getattr(cliente, campo) and (marcado or consents_sincronizados):
                        setattr(cliente, campo, marcado)
                        setattr(cliente, f'{campo}_em', agora)
                        setattr(cliente, f'{campo}_ip', ip_origem)
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

            # Preco da DATA DO ATENDIMENTO (profissional > base) com a promocao
            # vigente nesse dia — o mesmo valor do resumo do wizard.
            valor, promocao, valor_cheio = preco_com_promocao(procedimento, profissional, data_hora)

            atendimento = Atendimento.objects.create(
                cliente=cliente,
                profissional=profissional,
                procedimento=procedimento,
                data_hora_inicio=data_hora,
                data_hora_fim=data_hora_fim,
                valor_cobrado=valor,
                valor_original=valor_cheio if promocao else None,
                promocao=promocao,
                descricao_preco=f'Promoção {promocao.nome}' if promocao else None,
                status=Atendimento.STATUS_PENDENTE,
            )

            # Aceites com prova (IP, user-agent, SHA-256 do texto): Politica de
            # Privacidade (versao LGPD vigente) + termo(s) do procedimento.
            AceiteTermo.registrar(cliente, VersaoTermo.lgpd_vigente(), request, atendimento)
            for termo in termos_proc:
                if request.POST.get(f'aceite_termo_{termo.pk}') == 'on':
                    AceiteTermo.registrar(cliente, termo, request, atendimento)

            for form, respostas in anamneses:
                RespostaAnamnese.objects.create(
                    formulario=form, cliente=cliente,
                    atendimento=atendimento, respostas_json=respostas,
                    respondida_em=agora,
                )
            if anamneses:
                registrar_log(
                    None, 'Consentimento de dados de saude (LGPD art. 11) no agendamento',
                    'atendimento', atendimento.pk,
                    detalhes={
                        'cliente_id': cliente.pk,
                        'formularios': [form.pk for form, _respostas in anamneses],
                        'texto': TEXTO_CONSENTIMENTO_SAUDE,
                    },
                    request=request,
                )
            email_cliente = cliente.email
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

    usuario_prof = getattr(profissional, 'usuario', None)
    prof_email = getattr(usuario_prof, 'email', None)
    if prof_email:
        # Aprovar/rejeitar e POST dentro do portal: o e-mail so leva ate a agenda
        # do dia (GET).
        _enfileirar_email('enviar_aprovacao_profissional_email', prof_email, {
            'profissional': profissional.nome,
            'cliente': nome,
            'procedimento': procedimento.nome,
            'data_hora': data_formatada,
            'link_revisar': link_revisar_agenda(data_hora),
        })

    request.session['agendamento_sucesso'] = {
        'nome': nome,
        'procedimento': procedimento.nome,
        'profissional': profissional.nome,
        'data_hora': data_formatada,
        'valor': formatar_brl(valor) if valor is not None else 'A consultar',
        # cheio riscado + nome da promo quando a promocao reduziu o valor
        'valor_cheio': formatar_brl(valor_cheio) if promocao else '',
        'promocao': promocao.nome if promocao else '',
        'pendente': True,
        # So promete acompanhamento por e-mail se o backend entrega de fato
        'email': bool(email_cliente) and email_configurado(),
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
