"""Site publico, agendamento online e links da cliente (sem login)."""
from django.urls import path
from django.views.generic import TemplateView

from ..views.publico import (
    agenda_profissional,
    agendamento,
    agendamento_api,
    confirmacao,
    formularios,
    lgpd,
    lista_espera,
    nps,
    portal,
    site,
    termos,
    verificacao,
)

urlpatterns = [
    # ─── Público ───
    path('', site.home, name='inicio'),
    path('quem-somos/', site.quem_somos, name='quem_somos'),
    path('termos-de-uso/', site.termos_uso, name='termos_uso'),
    path('politica-de-privacidade/', site.politica_privacidade, name='politica_privacidade'),
    path('contato/', site.agenda_contato, name='agenda_contato'),
    path('promocoes/', site.promocoes, name='promocoes'),
    path('favicon.ico', site.favicon, name='favicon'),
    # ─── Serviços ───
    path('servicos/faciais/', site.servicos_faciais, name='servicos_faciais'),
    path('servicos/corporais/', site.servicos_corporais, name='servicos_corporais'),
    # ─── Páginas Públicas (Equipe, Especialidades, Depoimentos, Galeria) ───
    path('equipe/', site.equipe, name='equipe'),
    path('especialidades/', site.especialidades, name='especialidades'),
    path('depoimentos/', site.depoimentos, name='depoimentos'),
    path('galeria/', site.galeria, name='galeria'),
    path('servicos/detalhe/<slug:slug>/', site.servico_detalhe, name='servico_detalhe'),
    # ─── Agendamento Público (SEM LOGIN) ───
    path('agendamento/', agendamento.agendamento_publico, name='agendamento_publico'),
    path('agendar/<slug:slug>/', agenda_profissional.agendar_por_profissional, name='agendar_por_profissional'),
    path('agenda/<slug:slug>/feed.ics', agenda_profissional.ics_feed_profissional, name='ics_feed_profissional'),
    path('embed/agendar/', agenda_profissional.embed_agendar, name='embed_agendar'),
    path('agendamento/confirmar/', agendamento.confirmar_agendamento, name='confirmar_agendamento'),
    path('agendamento/sucesso/', agendamento.agendamento_sucesso, name='agendamento_sucesso'),
    path('agendamento/otp/solicitar/', verificacao.solicitar_otp_agendamento, name='solicitar_otp_agendamento'),
    path('agendamento/otp/verificar/', verificacao.verificar_otp_agendamento, name='verificar_otp_agendamento'),
    # ─── Confirmação de Presença (link público via WhatsApp) ───
    path('confirmar/<str:token>/', confirmacao.confirmar_presenca, name='confirmar_presenca'),
    # ─── Meus Agendamentos (login via OTP email) ───
    path('meus-agendamentos/', portal.meus_agendamentos, name='meus_agendamentos'),
    path('meus-agendamentos/otp/enviar/', verificacao.meus_agendamentos_enviar_otp, name='meus_agendamentos_enviar_otp'),
    path('meus-agendamentos/otp/verificar/', verificacao.meus_agendamentos_verificar_otp, name='meus_agendamentos_verificar_otp'),
    path('meus-agendamentos/sair/', verificacao.meus_agendamentos_logout, name='meus_agendamentos_logout'),
    # reagendar pelo portal: id + sessao do OTP (anti-IDOR), sem token
    path('meus-agendamentos/<int:pk>/reagendar/', portal.reagendar_agendamento_portal,
         name='reagendar_agendamento_portal'),
    path('meus-agendamentos/<int:pk>/horarios/', portal.reagendar_horarios_portal,
         name='reagendar_horarios_portal'),
    # ─── Lista de Espera (público) ───
    path('lista-espera/', lista_espera.lista_espera_publica, name='lista_espera_publica'),
    path('lista-espera/sucesso/', lista_espera.lista_espera_sucesso, name='lista_espera_sucesso'),
    # ─── NPS Web (público) ───
    path('nps/<str:token>/', nps.nps_web, name='nps_web'),
    # ─── Anamnese / Pesquisa públicas (link mago via WhatsApp/Email) ───
    # IMPORTANTE: rotas /obrigado/ devem vir ANTES de <str:token>/ p/ não colidir
    path('anamnese/obrigado/', formularios.anamnese_obrigado, name='anamnese_obrigado'),
    path('anamnese/<str:token>/', formularios.anamnese_publica, name='anamnese_publica'),
    path('pesquisa/obrigado/', formularios.pesquisa_obrigado, name='pesquisa_obrigado'),
    path('pesquisa/<str:token>/', formularios.pesquisa_publica, name='pesquisa_publica'),
    # ─── Assinatura de Termos (público) ───
    path('termo/<str:token>/', termos.termo_assinatura, name='termo_assinatura'),
    # ─── AJAX ───
    path('ajax/horarios-disponiveis/', agendamento_api.api_horarios_disponiveis, name='api_horarios_disponiveis'),
    path('ajax/dias-disponiveis/', agendamento_api.api_dias_disponiveis, name='api_dias_disponiveis'),
    path('ajax/cancelar-agendamento/', portal.cancelar_agendamento, name='cancelar_agendamento'),
    # ─── PWA ───
    path('manifest.json', TemplateView.as_view(template_name='pwa/manifest.json', content_type='application/json'), name='manifest'),
    path('painel/manifest.json', TemplateView.as_view(template_name='pwa/manifest_admin.json', content_type='application/json'), name='manifest_admin'),
    path('sw.js', TemplateView.as_view(template_name='pwa/sw.js', content_type='application/javascript'), name='sw'),
    # ─── LGPD ───
    path('lgpd/meus-dados/', lgpd.meus_dados, name='lgpd_meus_dados'),
    path('lgpd/unsubscribe/<str:token>/', lgpd.unsubscribe, name='lgpd_unsubscribe'),
]
