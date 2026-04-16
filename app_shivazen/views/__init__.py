# Views — imports explicitos (sem wildcard)
from .auth import (
    usuarioLogin, usuarioLogout,
    ShivaZenPasswordResetView, ShivaZenPasswordResetDoneView,
    ShivaZenPasswordResetConfirmView, ShivaZenPasswordResetCompleteView,
)
from .public import (
    home, termosUso, politicaPrivacidade, quemsomos, agendaContato, promocoes,
    equipe, especialidades, depoimentos, galeria, servico_detalhe,
    lista_espera_publica, lista_espera_sucesso,
)
from .services import servicos_faciais, servicos_corporais, servicos_produtos
from .ajax import buscar_procedimentos, buscar_horarios
from .admin import (
    prontuarioconsentimento, profissionalCadastro, profissionalEditar,
    admin_promocoes, admin_criar_promocao, admin_editar_promocao, admin_excluir_promocao,
    admin_auditoria, admin_atualizar_status,
)
from .dashboard import (
    painel, painel_overview, painel_agendamentos, painel_clientes,
    painel_profissionais, exportar_relatorio_excel,
)
from .booking import (
    agendamento_publico, api_horarios_disponiveis, confirmar_agendamento,
    agendamento_sucesso, meus_agendamentos, verificar_telefone,
    api_dias_disponiveis, cancelar_agendamento, reagendar_agendamento,
)
from .whatsapp import whatsapp_webhook, whatsapp_webhook_verify
from .notificacoes import confirmar_presenca, painel_notificacoes, admin_cancelar_agendamento
from .prontuario import prontuario_detalhe, prontuario_salvar, anotacao_sessao_salvar
from .pacotes import admin_pacotes, admin_criar_pacote, admin_editar_pacote, admin_vender_pacote
from .features import (
    admin_bloqueios, admin_criar_bloqueio, admin_excluir_bloqueio,
    admin_procedimentos, admin_criar_procedimento, admin_editar_procedimento,
    admin_cliente_detalhe, admin_lista_espera, admin_notificar_espera,
    nps_web, admin_termos, admin_criar_termo, termo_assinatura,
)
from .profissional import (
    agenda as profissional_agenda,
    marcar_realizado as profissional_marcar_realizado,
    anotar as profissional_anotar,
)
from .health import healthcheck
