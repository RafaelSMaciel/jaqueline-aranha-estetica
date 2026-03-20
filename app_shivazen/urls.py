# app_shivazen/urls.py
from django.urls import path
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

    # ─── Agendamento Público (SEM LOGIN) ───
    path('agendamento/', views.agendamento_publico, name='agendamento_publico'),
    path('agendamento/confirmar/', views.confirmar_agendamento, name='confirmar_agendamento'),
    path('agendamento/sucesso/', views.agendamento_sucesso, name='agendamento_sucesso'),

    # ─── Confirmação de Presença (link público via WhatsApp) ───
    path('confirmar/<str:token>/', views.confirmar_presenca, name='confirmar_presenca'),

    # ─── Meus Agendamentos (via celular) ───
    path('meus-agendamentos/', views.meus_agendamentos, name='meus_agendamentos'),

    # ─── Admin Login (URL oculta) ───
    path('admin-login/', views.usuarioLogin, name='usuarioLogin'),
    path('admin-logout/', views.usuarioLogout, name='usuarioLogout'),

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

    # ─── Vendas (CRUD) ───
    path('painel/vendas/', views.painel_vendas, name='painel_vendas'),
    path('painel/vendas/criar/', views.criar_venda, name='criar_venda'),
    path('painel/vendas/<int:pk>/editar/', views.editar_venda, name='editar_venda'),

    # ─── Orçamentos (CRUD) ───
    path('painel/orcamentos/', views.painel_orcamentos, name='painel_orcamentos'),
    path('painel/orcamentos/criar/', views.criar_orcamento, name='criar_orcamento'),
    path('painel/orcamentos/<int:pk>/editar/', views.editar_orcamento, name='editar_orcamento'),

    # ─── Notificações ───
    path('painel/notificacoes/', views.painel_notificacoes, name='painel_notificacoes'),
    path('painel/cancelar-agendamento/', views.admin_cancelar_agendamento, name='admin_cancelar_agendamento'),

    # ─── Auditoria ───
    path('painel/auditoria/', views.admin_auditoria, name='admin_auditoria'),

    # ─── Status Update (AJAX) ───
    path('painel/atualizar-status/', views.admin_atualizar_status, name='admin_atualizar_status'),

    # ─── Dashboard Admin (legado) ───
    path('dashboard-admin/', views.adminDashboard, name='adminDashboard'),

    # ─── Administrativo ───
    path('admin/agendamentos/', views.adminAgendamentos, name='adminAgendamentos'),
    path('admin/procedimentos/', views.adminProcedimentos, name='adminProcedimentos'),
    path('admin/bloqueios/', views.adminBloqueios, name='adminBloqueios'),
    path('admin/bloqueios/criar/', views.criarBloqueio, name='criarBloqueio'),
    path('admin/bloqueios/<int:bloqueio_id>/excluir/', views.excluirBloqueio, name='excluirBloqueio'),

    # ─── AJAX ───
    path('ajax/buscar-procedimentos/', views.buscar_procedimentos, name='buscar_procedimentos'),
    path('ajax/buscar-horarios/', views.buscar_horarios, name='buscar_horarios'),
    path('ajax/horarios-disponiveis/', views.api_horarios_disponiveis, name='api_horarios_disponiveis'),
    path('ajax/dias-disponiveis/', views.api_dias_disponiveis, name='api_dias_disponiveis'),
    path('ajax/verificar-telefone/', views.verificar_telefone, name='verificar_telefone'),
    path('ajax/cancelar-agendamento/', views.cancelar_agendamento, name='cancelar_agendamento'),

    # ─── Estoque (CRUD) ───
    path('painel/estoque/', views.painel_estoque, name='painel_estoque'),
    path('painel/estoque/criar/', views.criar_produto, name='criar_produto'),
    path('painel/estoque/<int:pk>/editar/', views.editar_produto, name='editar_produto'),
    path('painel/estoque/movimentar/', views.movimentar_estoque, name='movimentar_estoque'),
    path('painel/estoque/movimentacoes/', views.historico_movimentacoes, name='historico_movimentacoes'),
    path('painel/estoque/categorias/criar/', views.criar_categoria, name='criar_categoria'),

    # ─── WhatsApp Bot API ───
    path('api/whatsapp/webhook/', views.whatsapp_webhook, name='whatsapp_webhook'),
    path('api/whatsapp/verify/', views.whatsapp_webhook_verify, name='whatsapp_webhook_verify'),

    # ─── Setup (seed via URL, protegido por token) ───
    path('setup-seed/', views.setup_seed, name='setup_seed'),
]