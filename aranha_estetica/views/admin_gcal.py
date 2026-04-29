"""Endpoints OAuth Google Calendar (painel)."""
import logging
import secrets

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from ..decorators import staff_required
from ..models import Profissional
from ..services import gcal as gcal_svc

logger = logging.getLogger(__name__)


@staff_required
def gcal_connect(request, prof_id):
    prof = get_object_or_404(Profissional, pk=prof_id)
    if not gcal_svc.gcal_disponivel():
        messages.error(request, 'Google Calendar nao configurado (env vars ausentes ou libs nao instaladas).')
        return redirect('aranha:painel_profissionais')
    state = secrets.token_urlsafe(24)
    request.session['gcal_oauth_state'] = state
    request.session['gcal_oauth_prof_id'] = prof.pk
    url = gcal_svc.build_authorization_url(state=state)
    if not url:
        messages.error(request, 'Falha ao construir URL OAuth.')
        return redirect('aranha:painel_profissionais')
    return redirect(url)


@staff_required
def gcal_callback(request):
    code = request.GET.get('code', '')
    state = request.GET.get('state', '')
    expected_state = request.session.get('gcal_oauth_state', '')
    prof_id = request.session.get('gcal_oauth_prof_id')
    if not code or not state or state != expected_state or not prof_id:
        messages.error(request, 'Callback OAuth invalido.')
        return redirect('aranha:painel_profissionais')
    prof = get_object_or_404(Profissional, pk=prof_id)
    ok, detalhe = gcal_svc.handle_oauth_callback(prof, code)
    if ok:
        messages.success(request, f'Google Calendar conectado p/ {prof.nome}.')
    else:
        messages.error(request, f'Falha ao conectar: {detalhe}')
    request.session.pop('gcal_oauth_state', None)
    request.session.pop('gcal_oauth_prof_id', None)
    return redirect('aranha:painel_profissionais')


@staff_required
def gcal_pull(request, prof_id):
    prof = get_object_or_404(Profissional, pk=prof_id)
    if not prof.gcal_refresh_token:
        messages.warning(request, 'Profissional nao conectado ao Google Calendar.')
        return redirect('aranha:painel_profissionais')
    importados = gcal_svc.pull_eventos_externos(prof)
    messages.success(request, f'{importados} evento(s) externo(s) importado(s).')
    return redirect('aranha:painel_profissionais')
