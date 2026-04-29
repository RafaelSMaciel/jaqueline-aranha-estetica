# Views — imports explicitos (sem wildcard)
from .auth import (
    usuario_login, usuario_logout,
    ClinicaPasswordResetView, ClinicaPasswordResetDoneView,
    ClinicaPasswordResetConfirmView, ClinicaPasswordResetCompleteView,
)
from .public import (
    home, termos_uso, politica_privacidade, quem_somos, agenda_contato, promocoes,
    equipe, especialidades, depoimentos, galeria, servico_detalhe,
    lista_espera_publica, lista_espera_sucesso,
    servicos_faciais, servicos_corporais, servicos_produtos,
)
from .admin import (
    prontuario_consentimento,
    admin_auditoria, admin_atualizar_status,
)
from .admin_professional import (
    profissional_cadastro,
    profissional_editar,
)
from .admin_promotions import (
    admin_criar_promocao,
    admin_editar_promocao,
    admin_excluir_promocao,
    admin_promocoes,
    admin_disparar_promocao,
)
from .dashboard import (
    painel, painel_overview, painel_agendamentos, painel_clientes,
    painel_profissionais, exportar_relatorio_excel,
)
from .booking import (
    agendamento_publico, confirmar_agendamento,
    agendamento_sucesso, meus_agendamentos, reagendar_agendamento,
    solicitar_otp_agendamento, verificar_otp_agendamento,
    meus_agendamentos_enviar_otp, meus_agendamentos_verificar_otp,
    meus_agendamentos_logout,
)
from .booking_api import (
    api_dias_disponiveis,
    api_horarios_disponiveis,
    cancelar_agendamento,
    verificar_telefone,
    buscar_procedimentos,
    buscar_horarios,
)
from .whatsapp import whatsapp_webhook, zenvia_sms_webhook
from .notificacoes import confirmar_presenca, painel_notificacoes, admin_cancelar_agendamento
from .prontuario import prontuario_detalhe, prontuario_salvar, anotacao_sessao_salvar
from .pacotes import admin_pacotes, admin_criar_pacote, admin_editar_pacote, admin_vender_pacote
from .admin_management import (
    admin_bloqueios, admin_criar_bloqueio, admin_excluir_bloqueio,
    admin_procedimentos, admin_criar_procedimento, admin_editar_procedimento,
    admin_cliente_detalhe, admin_lista_espera, admin_notificar_espera,
    nps_web, admin_termos, admin_criar_termo, termo_assinatura,
    admin_email_preview,
    admin_aprovar_agendamento, admin_rejeitar_agendamento,
    admin_bulk_agendamentos,
)
from .profissional import (
    agenda as profissional_agenda,
    marcar_realizado as profissional_marcar_realizado,
    anotar as profissional_anotar,
    aprovar_agendamento as profissional_aprovar,
    rejeitar_agendamento as profissional_rejeitar,
)
from .health import healthcheck, liveness
from .cron import run_job as cron_run_job
from .lgpd import meus_dados as lgpd_meus_dados, unsubscribe as lgpd_unsubscribe, aceitar_cookies as lgpd_aceitar_cookies
from .admin_usuarios import (
    admin_usuarios, admin_criar_usuario, admin_editar_usuario,
    admin_resetar_senha_usuario, admin_desativar_usuario,
)
from .admin_config import (
    admin_configuracoes, admin_criar_configuracao,
    admin_editar_configuracao, admin_excluir_configuracao,
)
from .admin_termos_compliance import admin_termos_compliance
from .admin_2fa import admin_2fa_setup, admin_2fa_verify, admin_2fa_challenge
from .admin_calendar import admin_calendar, admin_calendar_events, admin_calendar_mover
from .admin_branding import admin_branding
from .agenda_publica import agendar_por_profissional, ics_feed_profissional, embed_agendar
from .webpush_views import webpush_public_key, webpush_subscribe, webpush_unsubscribe
from .admin_gcal import gcal_connect, gcal_callback, gcal_pull
from .admin_workflows import (
    admin_workflows, admin_workflow_form, admin_workflow_excluir,
    admin_workflow_toggle, admin_workflow_execucoes,
)
from .admin_anamnese import (
    admin_anamneses, admin_anamnese_form, admin_anamnese_excluir,
    admin_anamnese_respostas,
)
