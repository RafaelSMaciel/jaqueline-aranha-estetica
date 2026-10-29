"""Views de CRUD de profissionais — cadastro e edicao.

A validacao e o Form (forms/cadastros: ProfissionalForm para os dados e os procedimentos que realiza,
GradeSemanalForm para a grade); a view aplica a recusa por atendimento orfao (057912d), grava tudo
numa transacao e, ao desativar, revoga o feed ICS (decisao 30).
"""
import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ...decorators import LIMITE_ESCRITA_EQUIPE, escrita_painel, staff_required
from ...forms.cadastros import (
    DIA_NUMERO, DIAS_SEMANA, HORA_FIM_PADRAO, HORA_INICIO_PADRAO, GradeSemanalForm, ProfissionalForm,
)
from ...forms.painel import avisar_erros, erros_por_campo
from ...models import (
    AgendaHorario,
    Atendimento,
    Procedimento,
    Profissional,
    Habilitacao,
)
from ...services import links
from ...utils.auditoria import registrar_log
from ..comum import responder_link_ics

logger = logging.getLogger(__name__)


def _dias_do_profissional(profissional):
    """Linhas do form a partir da grade semanal gravada (AgendaHorario).

    O form suporta 1 janela por dia: mostra a primeira e informa quantas
    janelas extras existem (preservadas se o horario do dia nao mudar).
    """
    por_dia = {}
    for disp in AgendaHorario.objects.filter(
        profissional=profissional
    ).order_by('dia_semana', 'hora_inicio'):
        por_dia.setdefault(disp.dia_semana, []).append(disp)

    dias = []
    for key, nome in DIAS_SEMANA:
        janelas = por_dia.get(DIA_NUMERO[key], [])
        primeira = janelas[0] if janelas else None
        dias.append({
            'key': key,
            'nome': nome,
            'marcado': bool(janelas),
            'hora_inicio': primeira.hora_inicio.strftime('%H:%M') if primeira else HORA_INICIO_PADRAO,
            'hora_fim': primeira.hora_fim.strftime('%H:%M') if primeira else HORA_FIM_PADRAO,
            'janelas_extras': max(0, len(janelas) - 1),
        })
    return dias


def _campos(form):
    """O que a tela mostra nos campos: o enviado (re-render apos erro) ou o inicial."""
    if form.is_bound:
        return {
            'form_nome': form.data.get('nome', ''), 'form_especialidade': form.data.get('especialidade', ''),
            'form_ativo': bool(form.cleaned_data.get('ativo')), 'erros': erros_por_campo(form),
        }
    return {'form_nome': form['nome'].value() or '', 'form_especialidade': form['especialidade'].value() or '',
            'form_ativo': bool(form['ativo'].value()), 'erros': {}}


@staff_required
@ratelimit(key='user', rate=LIMITE_ESCRITA_EQUIPE, method='POST', block=True)
def profissional_cadastro(request):
    procedimentos = Procedimento.objects.filter(ativo=True).order_by('nome')

    if request.method != 'POST':
        form, grade = ProfissionalForm(initial={'ativo': True}), GradeSemanalForm()
    else:
        form, grade = ProfissionalForm(request.POST), GradeSemanalForm(request.POST)
        if not (form.is_valid() & grade.is_valid()):   # & : os dois forms validam (todos os avisos)
            avisar_erros(request, form)
            avisar_erros(request, grade)
        else:
            try:
                # Atomico: cadastro + grade semanal + habilitacoes sao tudo-ou-nada
                with transaction.atomic():
                    profissional = form.save()
                    AgendaHorario.objects.bulk_create([
                        AgendaHorario(profissional=profissional, dia_semana=DIA_NUMERO[key], hora_inicio=hi,
                                      hora_fim=hf)
                        for key, (hi, hf) in grade.janelas.items()
                    ])
                    Habilitacao.objects.bulk_create(
                        [Habilitacao(profissional=profissional, procedimento_id=pid)
                         for pid in form.cleaned_data['procedimentos']],
                        ignore_conflicts=True,
                    )
                registrar_log(request.user, f'Cadastrou profissional: {profissional.nome}', 'profissional',
                              profissional.pk, request=request)
                messages.success(request, f'Profissional {profissional.nome} cadastrado com sucesso!')
                return redirect('aranha:painel_profissionais')
            except (DatabaseError, ValidationError):
                logger.exception('profissional_cadastro_falhou', extra={'usuario_id': request.user.pk})
                messages.error(request, 'Erro ao cadastrar profissional. Verifique os dados e tente novamente.')

    return render(request, 'painel/cadastro_profissional.html', {
        'procedimentos': procedimentos,
        'dias': grade.linhas(),
        'procedimentos_marcados': form.cleaned_data.get('procedimentos', set()) if form.is_bound else set(),
        **_campos(form),
    })


def _salvar_janelas(profissional, janelas):
    """Aplica as janelas por dia sem apagar o que nao mudou.

    - Dia desmarcado: remove as janelas do dia.
    - Dia marcado com o mesmo horario da 1a janela exibida: mantem TODAS as
      janelas do dia (preserva turno dividido que o form nao consegue editar).
    - Dia marcado com horario novo: substitui as janelas do dia por uma.
    """
    existentes = {}
    for disp in AgendaHorario.objects.filter(
        profissional=profissional
    ).order_by('dia_semana', 'hora_inicio'):
        existentes.setdefault(disp.dia_semana, []).append(disp)

    for key, _nome in DIAS_SEMANA:
        num = DIA_NUMERO[key]
        atuais = existentes.get(num, [])
        if key not in janelas:
            if atuais:
                AgendaHorario.objects.filter(pk__in=[d.pk for d in atuais]).delete()
            continue
        hi, hf = janelas[key]
        if atuais and atuais[0].hora_inicio == hi and atuais[0].hora_fim == hf:
            continue
        if atuais:
            AgendaHorario.objects.filter(pk__in=[d.pk for d in atuais]).delete()
        AgendaHorario.objects.create(
            profissional=profissional, dia_semana=num, hora_inicio=hi, hora_fim=hf,
        )


def _salvar_habilitacoes(profissional, selecionados):
    """Sincroniza habilitacoes SO dos procedimentos ativos (os exibidos no form).

    Habilitacoes de procedimentos inativos nao aparecem no form e sao
    preservadas (reativar o procedimento nao exige re-habilitar a equipe).
    """
    ativos = set(Procedimento.objects.filter(ativo=True).values_list('pk', flat=True))
    desejados = selecionados & ativos
    atuais = set(
        Habilitacao.objects.filter(profissional=profissional, procedimento_id__in=ativos)
        .values_list('procedimento_id', flat=True)
    )
    remover = atuais - desejados
    if remover:
        Habilitacao.objects.filter(profissional=profissional, procedimento_id__in=remover).delete()
    Habilitacao.objects.bulk_create(
        [Habilitacao(profissional=profissional, procedimento_id=pid) for pid in desejados - atuais],
        ignore_conflicts=True,
    )


def _atendimentos_que_ficariam_orfaos(profissional, ativo, janelas, procedimentos, *, ativo_antes):
    """Atendimentos futuros que a edicao deixaria sem quem atenda: desativar, desmarcar o dia,
    mudar a janela do dia p/ fora do horario marcado ou tirar a habilitacao do procedimento.
    A edicao e recusada listando-os (reagendar/cancelar antes): antes os horarios ficavam na
    agenda, o lembrete D-1 confirmava presenca e a profissional nem via a lista.
    ativo_antes: o gravado (o ModelForm ja copiou o enviado p/ a instancia)."""
    futuros = list(Atendimento.objects.filter(
        profissional=profissional, status__in=Atendimento.STATUS_ATIVOS, data_hora_inicio__gte=timezone.now(),
    ).select_related('cliente', 'procedimento').order_by('data_hora_inicio'))
    if not futuros:
        return []
    if ativo_antes and not ativo:
        return futuros
    janela_por_dia = {DIA_NUMERO[k]: v for k, v in janelas.items()}
    primeira_atual = {}
    for disp in AgendaHorario.objects.filter(profissional=profissional).order_by('dia_semana', 'hora_inicio'):
        primeira_atual.setdefault(disp.dia_semana, (disp.hora_inicio, disp.hora_fim))
    ativos = set(Procedimento.objects.filter(ativo=True).values_list('pk', flat=True))
    atuais = set(Habilitacao.objects.filter(profissional=profissional).values_list('procedimento_id', flat=True))
    sem_habilitacao = (atuais & ativos) - procedimentos
    orfaos = []
    for at in futuros:
        inicio, fim = timezone.localtime(at.data_hora_inicio), timezone.localtime(at.data_hora_fim)
        dia = inicio.isoweekday()
        janela = janela_por_dia.get(dia)
        fora_da_janela = janela is None or (
            janela != primeira_atual.get(dia) and not (janela[0] <= inicio.time() and fim.time() <= janela[1]))
        if fora_da_janela or at.procedimento_id in sem_habilitacao:
            orfaos.append(at)
    return orfaos


def _recusa_orfaos(orfaos):
    """Texto da recusa (057912d): os 5 primeiros e o total."""
    lista = '; '.join(
        f"{timezone.localtime(a.data_hora_inicio):%d/%m %H:%M} {a.procedimento.nome} ({a.cliente.nome})"
        for a in orfaos[:5])
    mais = f' e mais {len(orfaos) - 5}' if len(orfaos) > 5 else ''
    return (f'{len(orfaos)} atendimento(s) futuro(s) ficariam sem quem atenda: {lista}{mais}. '
            'Reagende ou cancele antes (Painel > Agendamentos).')


@staff_required
@ratelimit(key='user', rate=LIMITE_ESCRITA_EQUIPE, method='POST', block=True)
def profissional_editar(request, pk=None):
    """Editar profissional existente"""
    if not pk:
        messages.error(request, 'Profissional não especificado.')
        return redirect('aranha:painel_profissionais')
    profissional = get_object_or_404(Profissional, pk=pk)
    procedimentos = Procedimento.objects.filter(ativo=True).order_by('nome')

    if request.method != 'POST':
        return render(request, 'painel/editar_profissional.html', {
            'profissional': profissional,
            'procedimentos': procedimentos,
            'procedimentos_atuais': list(
                Habilitacao.objects.filter(profissional=profissional).values_list('procedimento_id', flat=True)),
            'dias': _dias_do_profissional(profissional),
            **_campos(ProfissionalForm(instance=profissional)),
        })

    dados = request.POST.copy()
    dados.setdefault('nome', profissional.nome)                       # campo ausente = mantem
    dados.setdefault('especialidade', profissional.especialidade or '')
    ativo_antes = profissional.ativo
    form, grade = ProfissionalForm(dados, instance=profissional), GradeSemanalForm(dados)
    orfaos = []
    if not (form.is_valid() & grade.is_valid()):   # & : os dois forms validam (todos os avisos)
        avisar_erros(request, form)
        avisar_erros(request, grade)
    else:
        d = form.cleaned_data
        orfaos = _atendimentos_que_ficariam_orfaos(
            profissional, d['ativo'], grade.janelas, d['procedimentos'], ativo_antes=ativo_antes)
        if orfaos:
            messages.error(request, _recusa_orfaos(orfaos))
        else:
            try:
                with transaction.atomic():
                    form.save()
                    _salvar_janelas(profissional, grade.janelas)
                    _salvar_habilitacoes(profissional, d['procedimentos'])
                registrar_log(request.user, f'Editou profissional: {profissional.nome}', 'profissional',
                              profissional.pk, request=request)
                if ativo_antes and not profissional.ativo and links.revogar_feed_ics(
                        profissional, motivo='profissional desativado', request=request):
                    messages.info(request, 'O link do calendário (feed ICS) foi desativado. '
                                           'Ao reativar o profissional, gere um link novo.')
                messages.success(request, f'Profissional {profissional.nome} atualizado com sucesso!')
                return redirect('aranha:painel_profissionais')
            except (DatabaseError, ValidationError):
                logger.exception('profissional_edicao_falhou', extra={'profissional_id': profissional.pk})
                messages.error(request, 'Erro ao atualizar profissional. Verifique os dados e tente novamente.')

    # Nada e gravado/apagado: re-renderiza com o que foi digitado (a instancia volta ao gravado).
    profissional.refresh_from_db()
    return render(request, 'painel/editar_profissional.html', {
        'profissional': profissional,
        'procedimentos': procedimentos,
        'procedimentos_atuais': list(form.cleaned_data.get('procedimentos', set())),
        'dias': grade.linhas(),
        # recusa por atendimento orfao: atalho p/ a agenda do profissional (reagendar/cancelar)
        'orfaos_url': f"{reverse('aranha:painel_agendamentos')}?profissional={profissional.pk}" if orfaos else '',
        **_campos(form),
    })


@escrita_painel
def profissional_rotacionar_ics(request, pk):
    """Gera o link do feed ICS (o link antigo para de funcionar).

    Primeira geracao ou rotacao quando o link vazar (o token vai na URL do
    feed e fica em historico/logs de quem o recebeu).
    """
    profissional = get_object_or_404(Profissional, pk=pk)
    return responder_link_ics(
        request, profissional, template='painel/ics_link.html',
        voltar=reverse('aranha:painel_profissionais'), voltar_rotulo='Profissionais',
    )
