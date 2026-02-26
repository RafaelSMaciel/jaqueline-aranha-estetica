from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Q, Sum, F
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.urls import reverse
import json

from ..models import * 


def buscar_procedimentos(request):

    procedimentos = Procedimento.objects.filter(ativo=True).values('id_procedimento', 'nome', 'duracao_minutos', 'preco__valor')

    return JsonResponse({'procedimentos': list(procedimentos)})





def buscar_horarios(request):

    # Implementação simplificada para AJAX se necessário

    # Por enquanto o agendamento_publico já renderiza tudo

    return JsonResponse({'status': 'ok'})



