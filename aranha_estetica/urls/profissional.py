"""Portal do profissional (a propria agenda)."""
from django.urls import path

from ..views.profissional import portal

urlpatterns = [
    # ─── Portal do Profissional ───
    path('profissional/', portal.agenda, name='profissional_agenda'),
    path('profissional/atendimento/<int:pk>/realizado/', portal.marcar_realizado, name='profissional_marcar_realizado'),
    path('profissional/atendimento/<int:pk>/anotar/', portal.anotar, name='profissional_anotar'),
    path('profissional/atendimento/<int:pk>/aprovar/', portal.aprovar_agendamento, name='profissional_aprovar'),
    path('profissional/atendimento/<int:pk>/rejeitar/', portal.rejeitar_agendamento, name='profissional_rejeitar'),
    path('profissional/calendario/novo-link/', portal.gerar_link_calendario, name='profissional_link_calendario'),
]
