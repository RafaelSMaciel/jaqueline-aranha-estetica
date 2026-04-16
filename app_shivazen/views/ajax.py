from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from ..models import Procedimento


@require_GET
@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def buscar_procedimentos(request):
    """Retorna procedimentos ativos (endpoint público para agendamento)."""
    procedimentos = Procedimento.objects.filter(ativo=True).values(
        'id', 'nome', 'duracao_minutos'
    )
    return JsonResponse({'procedimentos': list(procedimentos)})


@require_GET
@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def buscar_horarios(request):
    """Retorna horarios disponiveis para um profissional em uma data.
    Params: profissional_id, data (YYYY-MM-DD), procedimento_id
    """
    from datetime import datetime
    from ..models import Profissional

    prof_id = request.GET.get('profissional_id')
    data_str = request.GET.get('data')
    if not prof_id or not data_str:
        return JsonResponse({'error': 'Parâmetros obrigatórios: profissional_id, data'}, status=400)

    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Data inválida. Use YYYY-MM-DD.'}, status=400)

    try:
        profissional = Profissional.objects.get(pk=prof_id, ativo=True)
    except Profissional.DoesNotExist:
        return JsonResponse({'error': 'Profissional não encontrado'}, status=404)

    horarios = profissional.get_horarios_disponiveis(data)
    return JsonResponse({'horarios': horarios})
