# app_shivazen/urls.py
from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

app_name = 'shivazen'

urlpatterns = [
    # --- Rotas Abertas (Públicas) ---
    path('', views.home, name='inicio'),
    path('quemsomos/', views.quemsomos, name='quemsomos'),
    path('termos-de-uso/', views.termosUso, name='termosUso'),
    path('politica-de-privacidade/', views.politicaPrivacidade, name='politicaPrivacidade'),
    path('contato/', views.agendaContato, name='agendaContato'),

    # --- Rotas de Serviços ---
    path('servicos/faciais/', views.servicos_faciais, name='servicos_faciais'),
    path('servicos/corporais/', views.servicos_corporais, name='servicos_corporais'),
    path('servicos/produtos/', views.servicos_produtos, name='servicos_produtos'),

    # --- Rotas de Autenticação ---
    path('cadastro/', views.usuarioCadastro, name='usuarioCadastro'),
    path('login/', views.usuarioLogin, name='usuarioLogin'),  
    path('logout/', views.usuarioLogout, name='usuarioLogout'),
    
    # --- Rotas de Recuperação de Senha ---
    path('esqueci-senha/', auth_views.PasswordResetView.as_view(template_name='usuario/esqueciSenha.html'), name='password_reset'),
    path('esqueci-senha/enviado/', auth_views.PasswordResetDoneView.as_view(template_name='usuario/esqueciSenhaEnviado.html'), name='password_reset_done'),
    path('esqueci-senha/confirmar/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='usuario/esqueciSenhaConfirmar.html'), name='password_reset_confirm'),
    path('esqueci-senha/completo/', auth_views.PasswordResetCompleteView.as_view(template_name='usuario/esqueciSenhaCompleto.html'), name='password_reset_complete'),

    # --- Rotas da Área Restrita ---
    # Painel Roteador (auto-detect user type)
    path('meu-painel/', views.painel, name='painel_redirect'),
    
    # Painel do Cliente (usuários comuns)
    path('cliente/painel/', views.painel_cliente, name='painel_cliente'),
    
    # Dashboard Admin (staff only)
    path('painel/', views.painel_overview, name='painel'),
    path('painel/agendamentos/', views.painel_agendamentos, name='painel_agendamentos'),
    path('painel/clientes/', views.painel_clientes, name='painel_clientes'),
    path('painel/profissionais/', views.painel_profissionais, name='painel_profissionais'),
    path('perfil/', views.perfil, name='perfil'),
    
    # Legacy routes
    path('dashboard-admin/', views.adminDashboard, name='adminDashboard'),
    path('agendamento/', views.agendamento_publico, name='agendamento_publico'),
    path('agendamento/confirmar/', views.confirmar_agendamento, name='confirmar_agendamento'),
    path('painel/prontuario/', views.prontuarioconsentimento, name='prontuarioconsentimento'),
    path('painel/cadastrar-profissional/', views.profissionalCadastro, name='profissionalCadastro'),
    path('painel/editar-profissional/', views.profissionalEditar, name='profissionalEditar'),
    
    # --- Rotas Administrativas ---
    path('admin/agendamentos/', views.adminAgendamentos, name='adminAgendamentos'),
    path('admin/procedimentos/', views.adminProcedimentos, name='adminProcedimentos'),
    path('admin/bloqueios/', views.adminBloqueios, name='adminBloqueios'),
    path('admin/bloqueios/criar/', views.criarBloqueio, name='criarBloqueio'),
    path('admin/bloqueios/<int:bloqueio_id>/excluir/', views.excluirBloqueio, name='excluirBloqueio'),

    # --- Rotas para chamadas AJAX do agendamento ---
    path('ajax/buscar-procedimentos/', views.buscar_procedimentos, name='buscar_procedimentos'),
    path('ajax/buscar-horarios/', views.buscar_horarios, name='buscar_horarios'),
]