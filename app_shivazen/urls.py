# app_shivazen/urls.py
from django.urls import path
from django.views.generic import TemplateView
from . import views

app_name = 'shivazen'

urlpatterns = [
    # ─── Público ───
    path('', views.home, name='inicio'),
    path('quemsomos/', views.quemsomos, name='quemsomos'),
    path('termos-de-uso/', views.termosUso, name='termosUso'),
    path('politica-de-privacidade/', views.politicaPrivacidade, name='politicaPrivacidade'),
    path('contato/', views.agendaContato, name='agendaContato'),
    path('promocoes/', views.promocoes, name='promocoes'),

    # ─── Serviços ───
    path('servicos/faciais/', views.servicos_faciais, name='servicos_faciais'),
    path('servicos/corporais/', views.servicos_corporais, name='servicos_corporais'),
    path('servicos/produtos/', views.servicos_produtos, name='servicos_produtos'),

    # ─── Páginas Públicas (Equipe, Especialidades, Depoimentos, Galeria) ───
    path('equipe/', views.equipe, name='equipe'),
    path('especialidades/', views.especialidades, name='especialidades'),
    path('depoimentos/', views.depoimentos, name='depoimentos'),
    path('galeria/', views.galeria, name='galeria'),
    path('servicos/detalhe/<slug:slug>/', views.servico_detalhe, name='servico_detalhe'),

    # ─── Agendamento Público (SEM LOGIN) ───
    path('agendamento/', views.agendamento_publico, name='agendamento_publico'),
    path('agendamento/confirmar/', views.confirmar_agendamento, name='confirmar_agendamento'),
    path('agendamento/sucesso/', views.agendamento_sucesso, name='agendamento_sucesso'),

    # ─── Confirmação de Presença (link público via WhatsApp) ───
    path('confirmar/<str:token>/', views.confirmar_presenca, name='confirmar_presenca'),

    # ─── Reagendamento Público (link via WhatsApp / meus-agendamentos) ───
    path('reagendar/<str:token>/', views.reagendar_agendamento, name='reagendar_agendamento'),

    # ─── Meus Agendamentos (via celular) ───
    path('meus-agendamentos/', views.meus_agendamentos, name='meus_agendamentos'),

    # ─── Lista de Espera (público) ───
    path('lista-espera/', views.lista_espera_publica, name='lista_espera_publica'),
    path('lista-espera/sucesso/', views.lista_espera_sucesso, name='lista_espera_sucesso'),

    # ─── Admin Login (URL oculta) ───
    path('admin-login/', views.usuarioLogin, name='usuarioLogin'),
    path('admin-logout/', views.usuarioLogout, name='usuarioLogout'),

    # ─── Recuperação de Senha ───
    path('admin-login/recuperar/', views.ShivaZenPasswordResetView.as_view(), name='password_reset'),
    path('admin-login/recuperar/enviado/', views.ShivaZenPasswordResetDoneView.as_view(), name='password_reset_done'),
    path('admin-login/recuperar/<uidb64>/<token>/', views.ShivaZenPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('admin-login/recuperar/completo/', views.ShivaZenPasswordResetCompleteView.as_view(), name='password_reset_complete'),

    # ─── Painel Administrativo (staff only) ───
    path('painel/', views.painel, name='painel'),
    path('painel/overview/', views.painel_overview, name='painel_overview'),
    path('painel/agendamentos/', views.painel_agendamentos, name='painel_agendamentos'),
    path('painel/clientes/', views.painel_clientes, name='painel_clientes'),
    path('painel/profissionais/', views.painel_profissionais, name='painel_profissionais'),
    path('painel/prontuario/', views.prontuarioconsentimento, name='prontuarioconsentimento'),
    path('painel/cadastrar-profissional/', views.profissionalCadastro, name='profissionalCadastro'),
    path('painel/editar-profissional/<int:pk>/', views.profissionalEditar, name='profissionalEditar'),
    path('painel/exportar-relatorio/', views.exportar_relatorio_excel, name='exportar_relatorio_excel'),

    # ─── Admin Promoções (CRUD) ───
    path('painel/promocoes/', views.admin_promocoes, name='admin_promocoes'),
    path('painel/promocoes/criar/', views.admin_criar_promocao, name='admin_criar_promocao'),
    path('painel/promocoes/<int:pk>/editar/', views.admin_editar_promocao, name='admin_editar_promocao'),
    path('painel/promocoes/<int:pk>/excluir/', views.admin_excluir_promocao, name='admin_excluir_promocao'),


    # ─── Notificações ───
    path('painel/notificacoes/', views.painel_notificacoes, name='painel_notificacoes'),
    path('painel/cancelar-agendamento/', views.admin_cancelar_agendamento, name='admin_cancelar_agendamento'),

    # ─── Prontuario ───
    path('painel/prontuario/<int:cliente_id>/', views.prontuario_detalhe, name='prontuario_detalhe'),
    path('painel/prontuario/<int:cliente_id>/salvar/', views.prontuario_salvar, name='prontuario_salvar'),
    path('painel/anotacao/<int:atendimento_id>/salvar/', views.anotacao_sessao_salvar, name='anotacao_sessao_salvar'),

    # ─── Pacotes ───
    path('painel/pacotes/', views.admin_pacotes, name='admin_pacotes'),
    path('painel/pacotes/criar/', views.admin_criar_pacote, name='admin_criar_pacote'),
    path('painel/pacotes/<int:pk>/editar/', views.admin_editar_pacote, name='admin_editar_pacote'),
    path('painel/pacotes/vender/', views.admin_vender_pacote, name='admin_vender_pacote'),

    # ─── Bloqueios de Agenda ───
    path('painel/bloqueios/', views.admin_bloqueios, name='admin_bloqueios'),
    path('painel/bloqueios/criar/', views.admin_criar_bloqueio, name='admin_criar_bloqueio'),
    path('painel/bloqueios/<int:bloqueio_id>/excluir/', views.admin_excluir_bloqueio, name='admin_excluir_bloqueio'),

    # ─── Procedimentos (CRUD) ───
    path('painel/procedimentos/', views.admin_procedimentos, name='admin_procedimentos'),
    path('painel/procedimentos/criar/', views.admin_criar_procedimento, name='admin_criar_procedimento'),
    path('painel/procedimentos/<int:pk>/editar/', views.admin_editar_procedimento, name='admin_editar_procedimento'),

    # ─── Cliente Detalhe ───
    path('painel/clientes/<int:pk>/', views.admin_cliente_detalhe, name='admin_cliente_detalhe'),

    # ─── Lista de Espera ───
    path('painel/lista-espera/', views.admin_lista_espera, name='admin_lista_espera'),
    path('painel/lista-espera/<int:pk>/notificar/', views.admin_notificar_espera, name='admin_notificar_espera'),

    # ─── Termos de Consentimento ───
    path('painel/termos/', views.admin_termos, name='admin_termos'),
    path('painel/termos/criar/', views.admin_criar_termo, name='admin_criar_termo'),

    # ─── NPS Web (público) ───
    path('nps/<str:token>/', views.nps_web, name='nps_web'),

    # ─── Assinatura de Termos (público) ───
    path('termo/<str:token>/', views.termo_assinatura, name='termo_assinatura'),

    # ─── Auditoria ───
    path('painel/auditoria/', views.admin_auditoria, name='admin_auditoria'),

    # ─── Status Update (AJAX) ───
    path('painel/atualizar-status/', views.admin_atualizar_status, name='admin_atualizar_status'),

    # ─── AJAX ───
    path('ajax/buscar-procedimentos/', views.buscar_procedimentos, name='buscar_procedimentos'),
    path('ajax/buscar-horarios/', views.buscar_horarios, name='buscar_horarios'),
    path('ajax/horarios-disponiveis/', views.api_horarios_disponiveis, name='api_horarios_disponiveis'),
    path('ajax/dias-disponiveis/', views.api_dias_disponiveis, name='api_dias_disponiveis'),
    path('ajax/verificar-telefone/', views.verificar_telefone, name='verificar_telefone'),
    path('ajax/cancelar-agendamento/', views.cancelar_agendamento, name='cancelar_agendamento'),


    # ─── Portal do Profissional ───
    path('profissional/', views.profissional_agenda, name='profissional_agenda'),
    path('profissional/atendimento/<int:pk>/realizado/', views.profissional_marcar_realizado, name='profissional_marcar_realizado'),
    path('profissional/atendimento/<int:pk>/anotar/', views.profissional_anotar, name='profissional_anotar'),
    path('profissional/atendimento/<int:pk>/aprovar/', views.profissional_aprovar, name='profissional_aprovar'),
    path('profissional/atendimento/<int:pk>/rejeitar/', views.profissional_rejeitar, name='profissional_rejeitar'),

    # ─── Healthcheck ───
    path('health/', views.healthcheck, name='healthcheck'),

    # ─── WhatsApp Bot API ───
    path('api/whatsapp/webhook/', views.whatsapp_webhook, name='whatsapp_webhook'),
    # ─── PWA (Admin) ───
    path('manifest.json', TemplateView.as_view(template_name='pwa/manifest.json', content_type='application/json'), name='manifest'),
    path('sw.js', TemplateView.as_view(template_name='pwa/sw.js', content_type='application/javascript'), name='sw'),
]