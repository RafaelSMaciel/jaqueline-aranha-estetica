from django.http import JsonResponse

from ..models import Procedimento


def buscar_procedimentos(request):
    procedimentos = Procedimento.objects.filter(ativo=True).values(
        'id_procedimento', 'nome', 'duracao_minutos'
    )
    return JsonResponse({'procedimentos': list(procedimentos)})


def buscar_horarios(request):
    return JsonResponse({'status': 'ok'})
