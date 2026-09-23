"""Views de CRUD de profissionais — cadastro e edicao."""
import logging
from datetime import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.shortcuts import get_object_or_404, redirect, render

from ..decorators import staff_required
from ..models import (
    DisponibilidadeProfissional,
    Procedimento,
    Profissional,
    Habilitacao,
)
from ..utils.audit import registrar_log

logger = logging.getLogger(__name__)

# Ordem de exibicao (seg..dom) e mapeamento p/ DisponibilidadeProfissional.dia_semana
# (1=Dom, 2=Seg, ..., 7=Sab).
DIAS_SEMANA = [
    ('segunda', 'Segunda-feira'),
    ('terca', 'Terça-feira'),
    ('quarta', 'Quarta-feira'),
    ('quinta', 'Quinta-feira'),
    ('sexta', 'Sexta-feira'),
    ('sabado', 'Sábado'),
    ('domingo', 'Domingo'),
]
DIA_NUMERO = {
    'segunda': 2, 'terca': 3, 'quarta': 4, 'quinta': 5,
    'sexta': 6, 'sabado': 7, 'domingo': 1,
}

HORA_INICIO_PADRAO = '09:00'
HORA_FIM_PADRAO = '18:00'


def _ativo_do_post(request):
    """Checkbox 'ativo' envia value="1" (templates); 'on' = checkbox sem value."""
    return request.POST.get('ativo') in ('1', 'on')


def _parse_hora(valor):
    """'HH:MM' ou 'HH:MM:SS' (input type=time) -> time; None se vazio/invalido."""
    valor = (valor or '').strip()
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(valor, fmt).time()
        except ValueError:
            continue
    return None


def _ler_janelas(post):
    """Le as janelas semanais do POST.

    Retorna (janelas, erros): janelas = {dia_key: (hora_inicio, hora_fim)} so p/
    dias marcados e validos. Dia marcado sem horas ou com fim <= inicio vira
    erro (nunca e descartado em silencio).
    """
    janelas, erros = {}, []
    for key, nome in DIAS_SEMANA:
        if post.get(f'trabalha_{key}') not in ('on', '1'):
            continue
        hi = _parse_hora(post.get(f'hora_inicio_{key}'))
        hf = _parse_hora(post.get(f'hora_fim_{key}'))
        if not hi or not hf:
            erros.append(f'{nome}: informe a hora de início e a de fim.')
        elif hf <= hi:
            erros.append(f'{nome}: a hora de fim deve ser depois da hora de início.')
        else:
            janelas[key] = (hi, hf)
    return janelas, erros


def _ids_procedimentos(post):
    return {int(x) for x in post.getlist('procedimentos') if str(x).isdigit()}


def _dias_do_post(post):
    """Linhas do form reconstruidas a partir do POST (re-render apos erro)."""
    return [
        {
            'key': key,
            'nome': nome,
            'marcado': post.get(f'trabalha_{key}') in ('on', '1'),
            'hora_inicio': post.get(f'hora_inicio_{key}') or HORA_INICIO_PADRAO,
            'hora_fim': post.get(f'hora_fim_{key}') or HORA_FIM_PADRAO,
            'janelas_extras': 0,
        }
        for key, nome in DIAS_SEMANA
    ]


def _dias_do_profissional(profissional):
    """Linhas do form a partir das disponibilidades gravadas.

    O form suporta 1 janela por dia: mostra a primeira e informa quantas
    janelas extras existem (preservadas se o horario do dia nao mudar).
    """
    por_dia = {}
    for disp in DisponibilidadeProfissional.objects.filter(
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


@staff_required
def profissional_cadastro(request):
    procedimentos = Procedimento.objects.filter(ativo=True).order_by('nome')

    if request.method == 'POST':
        nome = request.POST.get('nome', '').strip()
        especialidade = request.POST.get('especialidade', '').strip()
        ativo = _ativo_do_post(request)
        janelas, erros = _ler_janelas(request.POST)
        if not nome:
            erros.insert(0, 'O nome do profissional é obrigatório.')

        if erros:
            for erro in erros:
                messages.error(request, erro)
            return render(request, 'painel/cadastro_profissional.html', {
                'procedimentos': procedimentos,
                'dias': _dias_do_post(request.POST),
                'form_nome': nome,
                'form_especialidade': especialidade,
                'form_ativo': ativo,
                'procedimentos_marcados': _ids_procedimentos(request.POST),
            })

        try:
            # Atomico: cadastro + disponibilidades + habilitacoes sao tudo-ou-nada
            with transaction.atomic():
                profissional = Profissional.objects.create(
                    nome=nome,
                    especialidade=especialidade,
                    ativo=ativo,
                )
                DisponibilidadeProfissional.objects.bulk_create([
                    DisponibilidadeProfissional(
                        profissional=profissional,
                        dia_semana=DIA_NUMERO[key],
                        hora_inicio=hi,
                        hora_fim=hf,
                    )
                    for key, (hi, hf) in janelas.items()
                ])
                Habilitacao.objects.bulk_create(
                    [
                        Habilitacao(profissional=profissional, procedimento=proc)
                        for proc in procedimentos.filter(pk__in=_ids_procedimentos(request.POST))
                    ],
                    ignore_conflicts=True,
                )
            registrar_log(request.user, f'Cadastrou profissional: {nome}', 'profissional', profissional.pk)
            messages.success(request, f'Profissional {nome} cadastrado com sucesso!')
            return redirect('aranha:painel_profissionais')
        except (DatabaseError, ValidationError) as e:
            logger.error(f'Erro ao cadastrar profissional: {e}', exc_info=True)
            messages.error(request, 'Erro ao cadastrar profissional. Verifique os dados e tente novamente.')

    post = request.POST if request.method == 'POST' else None
    context = {
        'procedimentos': procedimentos,
        'dias': _dias_do_post(post) if post is not None else [
            {'key': k, 'nome': n, 'marcado': False, 'hora_inicio': HORA_INICIO_PADRAO,
             'hora_fim': HORA_FIM_PADRAO, 'janelas_extras': 0}
            for k, n in DIAS_SEMANA
        ],
        'form_nome': post.get('nome', '') if post is not None else '',
        'form_especialidade': post.get('especialidade', '') if post is not None else '',
        'form_ativo': _ativo_do_post(request) if post is not None else True,
        'procedimentos_marcados': _ids_procedimentos(post) if post is not None else set(),
    }
    return render(request, 'painel/cadastro_profissional.html', context)


def _salvar_janelas(profissional, janelas):
    """Aplica as janelas por dia sem apagar o que nao mudou.

    - Dia desmarcado: remove as janelas do dia.
    - Dia marcado com o mesmo horario da 1a janela exibida: mantem TODAS as
      janelas do dia (preserva turno dividido que o form nao consegue editar).
    - Dia marcado com horario novo: substitui as janelas do dia por uma.
    """
    existentes = {}
    for disp in DisponibilidadeProfissional.objects.filter(
        profissional=profissional
    ).order_by('dia_semana', 'hora_inicio'):
        existentes.setdefault(disp.dia_semana, []).append(disp)

    for key, _nome in DIAS_SEMANA:
        num = DIA_NUMERO[key]
        atuais = existentes.get(num, [])
        if key not in janelas:
            if atuais:
                DisponibilidadeProfissional.objects.filter(pk__in=[d.pk for d in atuais]).delete()
            continue
        hi, hf = janelas[key]
        if atuais and atuais[0].hora_inicio == hi and atuais[0].hora_fim == hf:
            continue
        if atuais:
            DisponibilidadeProfissional.objects.filter(pk__in=[d.pk for d in atuais]).delete()
        DisponibilidadeProfissional.objects.create(
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


@staff_required
def profissional_editar(request, pk=None):
    """Editar profissional existente"""
    if not pk:
        messages.error(request, 'Profissional não especificado.')
        return redirect('aranha:painel_profissionais')
    profissional = get_object_or_404(Profissional, pk=pk)
    procedimentos = Procedimento.objects.filter(ativo=True).order_by('nome')

    if request.method == 'POST':
        nome = request.POST.get('nome', profissional.nome).strip()
        especialidade = request.POST.get('especialidade', profissional.especialidade or '').strip()
        janelas, erros = _ler_janelas(request.POST)
        if not nome:
            erros.insert(0, 'O nome do profissional é obrigatório.')

        if erros:
            # Nada e gravado/apagado: re-renderiza com o que foi digitado.
            for erro in erros:
                messages.error(request, erro)
            return render(request, 'painel/editar_profissional.html', {
                'profissional': profissional,
                'procedimentos': procedimentos,
                'procedimentos_atuais': list(_ids_procedimentos(request.POST)),
                'dias': _dias_do_post(request.POST),
                'form_nome': nome,
                'form_especialidade': especialidade,
                'form_ativo': _ativo_do_post(request),
            })

        try:
            with transaction.atomic():
                profissional.nome = nome
                profissional.especialidade = especialidade
                profissional.ativo = _ativo_do_post(request)
                profissional.save()
                _salvar_janelas(profissional, janelas)
                _salvar_habilitacoes(profissional, _ids_procedimentos(request.POST))
            registrar_log(request.user, f'Editou profissional: {profissional.nome}', 'profissional', profissional.pk)
            messages.success(request, f'Profissional {profissional.nome} atualizado com sucesso!')
            return redirect('aranha:painel_profissionais')
        except (DatabaseError, ValidationError) as e:
            logger.error(f'Erro ao atualizar profissional: {e}', exc_info=True)
            messages.error(request, 'Erro ao atualizar profissional. Verifique os dados e tente novamente.')
            profissional.refresh_from_db()

    procedimentos_atuais = Habilitacao.objects.filter(
        profissional=profissional
    ).values_list('procedimento_id', flat=True)

    context = {
        'profissional': profissional,
        'procedimentos': procedimentos,
        'procedimentos_atuais': list(procedimentos_atuais),
        'dias': _dias_do_profissional(profissional),
        'form_nome': profissional.nome,
        'form_especialidade': profissional.especialidade or '',
        'form_ativo': profissional.ativo,
    }
    return render(request, 'painel/editar_profissional.html', context)
