from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from ..models import Procedimento


@require_GET
@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def buscar_procedimentos(request):
    """Retorna procedimentos ativos (endpoint público para agendamento)."""
    procedimentos = Procedimento.objects.filter(ativo=True).values(
        'id_procedimento', 'nome', 'duracao_minutos'
    )
    return JsonResponse({'procedimentos': list(procedimentos)})


@require_GET
@ratelimit(key='ip', rate='30/m', method='GET', block=True)
def buscar_horarios(request):
    """Placeholder para busca de horários."""
    return JsonResponse({'status': 'ok'})
