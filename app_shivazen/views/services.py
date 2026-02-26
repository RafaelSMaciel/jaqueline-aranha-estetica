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


def servicos_faciais(request):

    """Página de serviços faciais"""

    return render(request, 'servicos/faciais.html')





def servicos_corporais(request):

    """Página de serviços corporais"""

    return render(request, 'servicos/corporais.html')





def servicos_produtos(request):

    """Página de produtos"""

    return render(request, 'servicos/produtos.html')





# ========================================

# PAINEL - MODERN DASHBOARD VIEWS

# ========================================



from django.utils import timezone

from django.db.models import Q, Sum, F






