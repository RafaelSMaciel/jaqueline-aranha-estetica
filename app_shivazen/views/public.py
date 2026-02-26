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


def home(request):

    return render(request, 'inicio/home.html')





def termosUso(request):

    return render(request, 'inicio/termosUso.html')





def politicaPrivacidade(request):

    return render(request, 'inicio/politicaPrivacidade.html')





def quemsomos(request):

    return render(request, 'inicio/quemsomos.html')





def agendaContato(request):

    return render(request, 'agenda/contato.html')



# --- Autenticação e Cadastro (Refatorado) ---



