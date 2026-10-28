"""Painel da equipe (staff): agenda, clientes, prontuario, cadastros, financeiro e relatorios."""
from django.urls import path

from ..views.painel import (
    agendamentos,
    anamneses,
    aprovacao,
    auditoria,
    bloqueios,
    calendario,
    clientes,
    configuracoes,
    conformidade_termos,
    excecoes,
    financeiro,
    google_agenda,
    lista_espera,
    marca,
    notificacoes,
    pacotes,
    previa_email,
    procedimentos,
    profissionais,
    promocoes,
    prontuario,
    relatorios,
    retornos,
    termos,
    usuarios,
    visao_geral,
)

urlpatterns = [
    # ─── Google Calendar OAuth ───
    path('painel/integrations/google/connect/<int:prof_id>/', google_agenda.gcal_connect, name='gcal_connect'),
    path('painel/integrations/google/callback/', google_agenda.gcal_callback, name='gcal_callback'),
    path('painel/integrations/google/pull/<int:prof_id>/', google_agenda.gcal_pull, name='gcal_pull'),
    # ─── Anamnese ───
    path('painel/anamneses/', anamneses.admin_anamneses, name='admin_anamneses'),
    path('painel/anamneses/novo/', anamneses.admin_anamnese_form, name='admin_anamnese_criar'),
    path('painel/anamneses/<int:pk>/editar/', anamneses.admin_anamnese_form, name='admin_anamnese_editar'),
    path('painel/anamneses/<int:pk>/excluir/', anamneses.admin_anamnese_excluir, name='admin_anamnese_excluir'),
    path('painel/anamneses/<int:pk>/respostas/', anamneses.admin_anamnese_respostas, name='admin_anamnese_respostas'),
    # ─── Exceções de disponibilidade (folga / horário diferente por data) ───
    path('painel/profissionais/<int:prof_id>/excecoes/', excecoes.admin_excecoes, name='admin_excecoes'),
    path('painel/profissionais/<int:prof_id>/excecoes/criar/', excecoes.admin_excecao_criar, name='admin_excecao_criar'),
    path('painel/excecoes/<int:pk>/excluir/', excecoes.admin_excecao_excluir, name='admin_excecao_excluir'),
    # ─── Painel Administrativo (staff only) ───
    path('painel/', visao_geral.painel, name='painel'),
    path('painel/overview/', visao_geral.painel_overview, name='painel_overview'),
    path('painel/agendamentos/', visao_geral.painel_agendamentos, name='painel_agendamentos'),
    path('painel/clientes/', visao_geral.painel_clientes, name='painel_clientes'),
    path('painel/profissionais/', visao_geral.painel_profissionais, name='painel_profissionais'),
    path('painel/prontuario/', prontuario.prontuario_consentimento, name='prontuario_consentimento'),
    path('painel/cadastrar-profissional/', profissionais.profissional_cadastro, name='profissional_cadastro'),
    path('painel/editar-profissional/<int:pk>/', profissionais.profissional_editar, name='profissional_editar'),
    path('painel/profissionais/<int:pk>/ics/novo-token/', profissionais.profissional_rotacionar_ics, name='profissional_rotacionar_ics'),
    path('painel/exportar-relatorio/', visao_geral.exportar_relatorio_excel, name='exportar_relatorio_excel'),
    # ─── Admin Promoções (CRUD) ───
    path('painel/promocoes/', promocoes.admin_promocoes, name='admin_promocoes'),
    path('painel/promocoes/criar/', promocoes.admin_criar_promocao, name='admin_criar_promocao'),
    path('painel/promocoes/<int:pk>/editar/', promocoes.admin_editar_promocao, name='admin_editar_promocao'),
    path('painel/promocoes/<int:pk>/excluir/', promocoes.admin_excluir_promocao, name='admin_excluir_promocao'),
    path('painel/promocoes/<int:pk>/disparar/', promocoes.admin_disparar_promocao, name='admin_disparar_promocao'),
    # ─── Relatórios (Financeiro / NPS / Comissões) ───
    path('painel/financeiro/', financeiro.dashboard_financeiro, name='dashboard_financeiro'),
    path('painel/nps/', relatorios.painel_nps, name='painel_nps'),
    path('painel/nps/<int:pk>/publicacao/', relatorios.admin_nps_publicacao, name='admin_nps_publicacao'),
    path('painel/comissoes/', relatorios.painel_comissoes, name='painel_comissoes'),
    path('painel/comissoes/<int:pk>/pagar/', relatorios.admin_comissao_pagar, name='admin_comissao_pagar'),
    # ─── Notificações ───
    path('painel/notificacoes/', notificacoes.painel_notificacoes, name='painel_notificacoes'),
    # ─── Prontuario ───
    path('painel/prontuario/<int:cliente_id>/', prontuario.prontuario_detalhe, name='prontuario_detalhe'),
    path('painel/prontuario/<int:cliente_id>/salvar/', prontuario.prontuario_salvar, name='prontuario_salvar'),
    path('painel/anotacao/<int:atendimento_id>/salvar/', prontuario.anotacao_sessao_salvar, name='anotacao_sessao_salvar'),
    # ─── Pacotes ───
    path('painel/pacotes/', pacotes.admin_pacotes, name='admin_pacotes'),
    path('painel/pacotes/criar/', pacotes.admin_criar_pacote, name='admin_criar_pacote'),
    path('painel/pacotes/<int:pk>/editar/', pacotes.admin_editar_pacote, name='admin_editar_pacote'),
    path('painel/pacotes/vender/', pacotes.admin_vender_pacote, name='admin_vender_pacote'),
    path('painel/pacotes/compras/<int:pk>/cancelar/', pacotes.admin_cancelar_compra_pacote, name='admin_cancelar_compra_pacote'),
    # ─── Bloqueios de Agenda ───
    path('painel/bloqueios/', bloqueios.admin_bloqueios, name='admin_bloqueios'),
    path('painel/bloqueios/criar/', bloqueios.admin_criar_bloqueio, name='admin_criar_bloqueio'),
    path('painel/bloqueios/<int:bloqueio_id>/excluir/', bloqueios.admin_excluir_bloqueio, name='admin_excluir_bloqueio'),
    # ─── Procedimentos (CRUD) ───
    path('painel/procedimentos/', procedimentos.admin_procedimentos, name='admin_procedimentos'),
    path('painel/procedimentos/criar/', procedimentos.admin_criar_procedimento, name='admin_criar_procedimento'),
    path('painel/procedimentos/<int:pk>/editar/', procedimentos.admin_editar_procedimento, name='admin_editar_procedimento'),
    # ─── Cliente Detalhe ───
    path('painel/clientes/<int:pk>/', clientes.admin_cliente_detalhe, name='admin_cliente_detalhe'),
    # ─── Lista de Espera ───
    path('painel/lista-espera/', lista_espera.admin_lista_espera, name='admin_lista_espera'),
    path('painel/lista-espera/<int:pk>/notificar/', lista_espera.admin_notificar_espera, name='admin_notificar_espera'),
    # ─── Termos de Consentimento ───
    path('painel/termos/', termos.admin_termos, name='admin_termos'),
    path('painel/termos/criar/', termos.admin_criar_termo, name='admin_criar_termo'),
    path('painel/termos/compliance/', conformidade_termos.admin_termos_compliance, name='admin_termos_compliance'),
    # ─── Auditoria ───
    path('painel/auditoria/', auditoria.admin_auditoria, name='admin_auditoria'),
    # ─── Status Update (AJAX) ───
    path('painel/atualizar-status/', agendamentos.admin_atualizar_status, name='admin_atualizar_status'),
    # ─── Aprovação gerente (PENDENTE → AGENDADO / CANCELADO) ───
    path('painel/agendamentos/<int:pk>/aprovar/', aprovacao.admin_aprovar_agendamento, name='admin_aprovar_agendamento'),
    path('painel/agendamentos/<int:pk>/rejeitar/', aprovacao.admin_rejeitar_agendamento, name='admin_rejeitar_agendamento'),
    path('painel/agendamentos/bulk/', aprovacao.admin_bulk_agendamentos, name='admin_bulk_agendamentos'),
    # Agendamento interno pela recepcao (sem OTP) + link do termo do atendimento
    path('painel/agendamentos/novo/', agendamentos.admin_agendamento_novo, name='admin_agendamento_novo'),
    path('painel/agendamentos/<int:pk>/termo-link/', termos.admin_gerar_link_termo, name='admin_gerar_link_termo'),
    path('painel/agendamentos/<int:pk>/ficha-link/', anamneses.admin_link_ficha, name='admin_link_ficha'),
    path('painel/retornos/', retornos.painel_retornos, name='painel_retornos'),
    path('painel/agendamentos/<int:pk>/retorno/', retornos.admin_agendar_retorno, name='admin_agendar_retorno'),
    path('painel/agendamentos/<int:pk>/valor/', agendamentos.admin_atendimento_valor, name='admin_atendimento_valor'),
    path('painel/agendamentos/<int:pk>/desfazer-realizacao/', agendamentos.admin_desfazer_realizacao,
         name='admin_desfazer_realizacao'),
    # ─── Usuarios Admin (CRUD + reset senha + ativar/desativar) ───
    path('painel/usuarios/', usuarios.admin_usuarios, name='admin_usuarios'),
    path('painel/usuarios/criar/', usuarios.admin_criar_usuario, name='admin_criar_usuario'),
    path('painel/usuarios/<int:pk>/editar/', usuarios.admin_editar_usuario, name='admin_editar_usuario'),
    path('painel/usuarios/<int:pk>/resetar-senha/', usuarios.admin_resetar_senha_usuario, name='admin_resetar_senha_usuario'),
    path('painel/usuarios/<int:pk>/toggle-ativo/', usuarios.admin_desativar_usuario, name='admin_desativar_usuario'),
    # ─── Configuracoes do Sistema (chave-valor editavel via painel) ───
    path('painel/configuracoes/', configuracoes.admin_configuracoes, name='admin_configuracoes'),
    path('painel/configuracoes/criar/', configuracoes.admin_criar_configuracao, name='admin_criar_configuracao'),
    path('painel/configuracoes/<int:pk>/editar/', configuracoes.admin_editar_configuracao, name='admin_editar_configuracao'),
    path('painel/configuracoes/<int:pk>/excluir/', configuracoes.admin_excluir_configuracao, name='admin_excluir_configuracao'),
    # ─── Branding (logo, cores, textos editaveis via UI) ───
    path('painel/branding/', marca.admin_branding, name='admin_branding'),
    # ─── Calendario (FullCalendar drag-drop) ───
    path('painel/calendario/', calendario.admin_calendar, name='admin_calendar'),
    path('painel/calendario/eventos/', calendario.admin_calendar_events, name='admin_calendar_events'),
    path('painel/calendario/mover/', calendario.admin_calendar_mover, name='admin_calendar_mover'),
    # ─── Email Preview (staff debug) ───
    path('painel/email-preview/', previa_email.admin_email_preview, name='admin_email_preview'),
    path('painel/email-preview/<str:nome>/', previa_email.admin_email_preview, name='admin_email_preview_nome'),
]
