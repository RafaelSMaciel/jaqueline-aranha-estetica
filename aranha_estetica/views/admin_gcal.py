"""Endpoints OAuth Google Calendar (painel).

Integracao opcional: sem libs google + env OAuth (gcal_disponivel() False)
as rotas respondem 404 e o painel esconde Conectar/Sincronizar.
"""
import logging
import secrets

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from ..decorators import staff_required
from ..models import Profissional
from ..services import gcal as gcal_svc

logger = logging.getLogger(__name__)


def _exigir_gcal():
    if not gcal_svc.gcal_disponivel():
        raise Http404('Google Calendar não configurado.')


@staff_required
def gcal_connect(request, prof_id):
    _exigir_gcal()
    prof = get_object_or_404(Profissional, pk=prof_id)
    state = secrets.token_urlsafe(24)
    request.session['gcal_oauth_state'] = state
    request.session['gcal_oauth_prof_id'] = prof.pk
    url, code_verifier = gcal_svc.build_authorization_url(state=state)
    if not url:
        messages.error(request, 'Falha ao iniciar a conexão com o Google Calendar.')
        return redirect('aranha:painel_profissionais')
    # PKCE: o callback cria outro Flow e precisa do mesmo code_verifier
    request.session['gcal_oauth_code_verifier'] = code_verifier or ''
    return redirect(url)


@staff_required
def gcal_callback(request):
    _exigir_gcal()
    code = request.GET.get('code', '')
    state = request.GET.get('state', '')
    expected_state = request.session.get('gcal_oauth_state', '')
    prof_id = request.session.get('gcal_oauth_prof_id')
    code_verifier = request.session.get('gcal_oauth_code_verifier') or None
    for chave in ('gcal_oauth_state', 'gcal_oauth_prof_id', 'gcal_oauth_code_verifier'):
        request.session.pop(chave, None)
    if not code or not state or state != expected_state or not prof_id:
        messages.error(request, 'Retorno do Google inválido. Tente conectar novamente.')
        return redirect('aranha:painel_profissionais')
    prof = get_object_or_404(Profissional, pk=prof_id)
    ok, detalhe = gcal_svc.handle_oauth_callback(prof, code, code_verifier=code_verifier)
    if ok:
        messages.success(request, f'Google Calendar conectado para {prof.nome}.')
    else:
        messages.error(request, f'Falha ao conectar: {detalhe}')
    return redirect('aranha:painel_profissionais')


@staff_required
@require_POST
def gcal_pull(request, prof_id):
    """Importa eventos externos como bloqueios (POST: altera dados)."""
    _exigir_gcal()
    prof = get_object_or_404(Profissional, pk=prof_id)
    if not prof.gcal_refresh_token:
        messages.warning(request, 'Profissional não conectado ao Google Calendar.')
        return redirect('aranha:painel_profissionais')
    importados = gcal_svc.pull_eventos_externos(prof)
    messages.success(request, f'{importados} evento(s) externo(s) importado(s).')
    return redirect('aranha:painel_profissionais')
