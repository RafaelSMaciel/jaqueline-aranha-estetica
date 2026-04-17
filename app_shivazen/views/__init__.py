# Views — imports explicitos (sem wildcard)
from .auth import (
    usuario_login, usuario_logout,
    ShivaZenPasswordResetView, ShivaZenPasswordResetDoneView,
    ShivaZenPasswordResetConfirmView, ShivaZenPasswordResetCompleteView,
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
from .whatsapp import whatsapp_webhook
from .notificacoes import confirmar_presenca, painel_notificacoes, admin_cancelar_agendamento
from .prontuario import prontuario_detalhe, prontuario_salvar, anotacao_sessao_salvar
from .pacotes import admin_pacotes, admin_criar_pacote, admin_editar_pacote, admin_vender_pacote
from .admin_management import (
    admin_bloqueios, admin_criar_bloqueio, admin_excluir_bloqueio,
    admin_procedimentos, admin_criar_procedimento, admin_editar_procedimento,
    admin_cliente_detalhe, admin_lista_espera, admin_notificar_espera,
    nps_web, admin_termos, admin_criar_termo, termo_assinatura,
)
from .profissional import (
    agenda as profissional_agenda,
    marcar_realizado as profissional_marcar_realizado,
    anotar as profissional_anotar,
    aprovar_agendamento as profissional_aprovar,
    rejeitar_agendamento as profissional_rejeitar,
)
from .health import healthcheck
from .lgpd import meus_dados as lgpd_meus_dados, unsubscribe as lgpd_unsubscribe, aceitar_cookies as lgpd_aceitar_cookies
