# app_shivazen/admin.py
from django.contrib import admin
from .models import (
    Funcionalidade, Perfil, PerfilFuncionalidade,
    Profissional, DisponibilidadeProfissional, BloqueioAgenda, ProfissionalProcedimento,
    Procedimento, Preco, Promocao,
    Cliente,
    Prontuario, ProntuarioPergunta, ProntuarioResposta, AnotacaoSessao,
    VersaoTermo, AceitePrivacidade, AssinaturaTermoProcedimento,
    Atendimento, Notificacao,
    AvaliacaoNPS,
    Pacote, ItemPacote, PacoteCliente, SessaoPacote,
    ListaEspera,
    LogAuditoria, ConfiguracaoSistema, CodigoVerificacao,
    Usuario,
)


# =====================================================================
# CONTROLE DE ACESSO
# =====================================================================

@admin.register(Funcionalidade)
class FuncionalidadeAdmin(admin.ModelAdmin):
    list_display = ('nome', 'descricao')
    search_fields = ('nome',)
    ordering = ('nome',)


@admin.register(Perfil)
class PerfilAdmin(admin.ModelAdmin):
    list_display = ('nome', 'descricao')
    search_fields = ('nome',)
    ordering = ('nome',)


@admin.register(PerfilFuncionalidade)
class PerfilFuncionalidadeAdmin(admin.ModelAdmin):
    list_display = ('perfil', 'funcionalidade')
    list_filter = ('perfil',)
    search_fields = ('perfil__nome', 'funcionalidade__nome')


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('email', 'nome', 'perfil', 'profissional', 'ativo')
    list_filter = ('perfil', 'ativo')
    search_fields = ('nome', 'email')
    ordering = ('email',)
    autocomplete_fields = ('perfil', 'profissional')


# =====================================================================
# PROFISSIONAIS
# =====================================================================

@admin.register(Profissional)
class ProfissionalAdmin(admin.ModelAdmin):
    list_display = ('nome', 'especialidade', 'ativo')
    list_filter = ('ativo',)
    search_fields = ('nome', 'especialidade')
    ordering = ('-ativo', 'nome')


@admin.register(DisponibilidadeProfissional)
class DisponibilidadeProfissionalAdmin(admin.ModelAdmin):
    list_display = ('profissional', 'dia_semana', 'hora_inicio', 'hora_fim')
    list_filter = ('profissional', 'dia_semana')
    search_fields = ('profissional__nome',)
    ordering = ('profissional', 'dia_semana', 'hora_inicio')
    autocomplete_fields = ('profissional',)


@admin.register(BloqueioAgenda)
class BloqueioAgendaAdmin(admin.ModelAdmin):
    list_display = ('profissional', 'data_hora_inicio', 'data_hora_fim', 'motivo')
    list_filter = ('profissional',)
    search_fields = ('profissional__nome', 'motivo')
    ordering = ('-data_hora_inicio',)
    date_hierarchy = 'data_hora_inicio'
    autocomplete_fields = ('profissional',)


@admin.register(ProfissionalProcedimento)
class ProfissionalProcedimentoAdmin(admin.ModelAdmin):
    list_display = ('profissional', 'procedimento')
    list_filter = ('profissional',)
    search_fields = ('profissional__nome', 'procedimento__nome')
    autocomplete_fields = ('profissional', 'procedimento')


# =====================================================================
# PROCEDIMENTOS E PREÇOS
# =====================================================================

@admin.register(Procedimento)
class ProcedimentoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'categoria', 'duracao_minutos', 'ativo')
    list_filter = ('categoria', 'ativo')
    search_fields = ('nome', 'descricao')
    prepopulated_fields = {'slug': ('nome',)}
    ordering = ('-ativo', 'categoria', 'nome')


@admin.register(Preco)
class PrecoAdmin(admin.ModelAdmin):
    list_display = ('procedimento', 'profissional', 'valor', 'vigente_desde')
    list_filter = ('profissional', 'vigente_desde')
    search_fields = ('procedimento__nome', 'profissional__nome')
    ordering = ('-vigente_desde', 'procedimento')
    autocomplete_fields = ('procedimento', 'profissional')


@admin.register(Promocao)
class PromocaoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'procedimento', 'desconto_percentual', 'data_inicio', 'data_fim', 'ativa')
    list_filter = ('ativa', 'data_inicio')
    search_fields = ('nome', 'procedimento__nome')
    ordering = ('-ativa', '-data_inicio')
    date_hierarchy = 'data_inicio'
    autocomplete_fields = ('procedimento',)


# =====================================================================
# CLIENTES
# =====================================================================

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nome_completo', 'telefone', 'email', 'faltas_consecutivas', 'bloqueado_online', 'ativo', 'criado_em')
    list_filter = ('ativo', 'bloqueado_online')
    search_fields = ('nome_completo', 'telefone', 'email', 'cpf')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('criado_em',)


# =====================================================================
# PRONTUÁRIO
# =====================================================================

@admin.register(Prontuario)
class ProntuarioAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'atualizado_em')
    search_fields = ('cliente__nome_completo',)
    ordering = ('-atualizado_em',)
    autocomplete_fields = ('cliente',)


@admin.register(ProntuarioPergunta)
class ProntuarioPerguntaAdmin(admin.ModelAdmin):
    list_display = ('texto', 'tipo_resposta', 'ativa')
    list_filter = ('tipo_resposta', 'ativa')
    search_fields = ('texto',)


@admin.register(ProntuarioResposta)
class ProntuarioRespostaAdmin(admin.ModelAdmin):
    list_display = ('prontuario', 'pergunta', 'atualizado_em')
    search_fields = ('prontuario__cliente__nome_completo', 'pergunta__texto')
    autocomplete_fields = ('prontuario', 'pergunta')


@admin.register(AnotacaoSessao)
class AnotacaoSessaoAdmin(admin.ModelAdmin):
    list_display = ('atendimento', 'usuario', 'criado_em')
    search_fields = ('atendimento__cliente__nome_completo', 'texto')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    autocomplete_fields = ('atendimento', 'usuario')


# =====================================================================
# TERMOS DE CONSENTIMENTO
# =====================================================================

@admin.register(VersaoTermo)
class VersaoTermoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'tipo', 'versao', 'procedimento', 'vigente_desde', 'ativa')
    list_filter = ('tipo', 'ativa')
    search_fields = ('titulo', 'procedimento__nome', 'versao')
    ordering = ('-ativa', '-vigente_desde')
    autocomplete_fields = ('procedimento',)


@admin.register(AceitePrivacidade)
class AceitePrivacidadeAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'versao_termo', 'ip', 'criado_em')
    search_fields = ('cliente__nome_completo', 'versao_termo__titulo')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    autocomplete_fields = ('cliente', 'versao_termo')


@admin.register(AssinaturaTermoProcedimento)
class AssinaturaTermoProcedimentoAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'versao_termo', 'atendimento', 'ip', 'criado_em')
    search_fields = ('cliente__nome_completo', 'versao_termo__titulo')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    autocomplete_fields = ('cliente', 'versao_termo', 'atendimento')


# =====================================================================
# AGENDAMENTO
# =====================================================================

@admin.register(Atendimento)
class AtendimentoAdmin(admin.ModelAdmin):
    list_display = ('data_hora_inicio', 'cliente', 'profissional', 'procedimento', 'status', 'valor_cobrado')
    list_filter = ('status', 'profissional', 'procedimento')
    search_fields = ('cliente__nome_completo', 'cliente__telefone', 'profissional__nome')
    ordering = ('-data_hora_inicio',)
    date_hierarchy = 'data_hora_inicio'
    readonly_fields = ('criado_em', 'token_cancelamento')
    autocomplete_fields = ('cliente', 'profissional', 'procedimento', 'promocao', 'reagendado_de')


@admin.register(Notificacao)
class NotificacaoAdmin(admin.ModelAdmin):
    list_display = ('atendimento', 'tipo', 'canal', 'status_envio', 'resposta_cliente', 'enviado_em', 'criado_em')
    list_filter = ('tipo', 'canal', 'status_envio', 'resposta_cliente')
    search_fields = ('atendimento__cliente__nome_completo', 'mensagem')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('token', 'criado_em')
    autocomplete_fields = ('atendimento',)


# =====================================================================
# AVALIAÇÃO NPS
# =====================================================================

@admin.register(AvaliacaoNPS)
class AvaliacaoNPSAdmin(admin.ModelAdmin):
    list_display = ('atendimento', 'nota', 'alerta_enviado', 'criado_em')
    list_filter = ('nota', 'alerta_enviado')
    search_fields = ('atendimento__cliente__nome_completo', 'comentario')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    autocomplete_fields = ('atendimento',)


# =====================================================================
# PACOTES
# =====================================================================

class ItemPacoteInline(admin.TabularInline):
    model = ItemPacote
    extra = 1
    autocomplete_fields = ('procedimento',)


@admin.register(Pacote)
class PacoteAdmin(admin.ModelAdmin):
    list_display = ('nome', 'preco_total', 'validade_meses', 'ativo')
    list_filter = ('ativo',)
    search_fields = ('nome', 'descricao')
    ordering = ('-ativo', 'nome')
    inlines = [ItemPacoteInline]


@admin.register(ItemPacote)
class ItemPacoteAdmin(admin.ModelAdmin):
    list_display = ('pacote', 'procedimento', 'quantidade_sessoes')
    search_fields = ('pacote__nome', 'procedimento__nome')
    autocomplete_fields = ('pacote', 'procedimento')


@admin.register(PacoteCliente)
class PacoteClienteAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'pacote', 'status', 'valor_pago', 'data_expiracao', 'criado_em')
    list_filter = ('status',)
    search_fields = ('cliente__nome_completo', 'pacote__nome')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    autocomplete_fields = ('cliente', 'pacote')


@admin.register(SessaoPacote)
class SessaoPacoteAdmin(admin.ModelAdmin):
    list_display = ('pacote_cliente', 'atendimento', 'criado_em')
    search_fields = (
        'pacote_cliente__cliente__nome_completo',
        'pacote_cliente__pacote__nome',
    )
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    autocomplete_fields = ('pacote_cliente', 'atendimento')


# =====================================================================
# LISTA DE ESPERA
# =====================================================================

@admin.register(ListaEspera)
class ListaEsperaAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'procedimento', 'profissional_desejado', 'data_desejada', 'turno_desejado', 'notificado', 'criado_em')
    list_filter = ('notificado', 'turno_desejado')
    search_fields = ('cliente__nome_completo', 'procedimento__nome')
    ordering = ('-criado_em',)
    date_hierarchy = 'data_desejada'
    autocomplete_fields = ('cliente', 'procedimento', 'profissional_desejado')


# =====================================================================
# AUDITORIA E SISTEMA
# =====================================================================

@admin.register(LogAuditoria)
class LogAuditoriaAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'usuario', 'acao', 'tabela_afetada', 'id_registro_afetado')
    list_filter = ('tabela_afetada',)
    search_fields = ('acao', 'usuario__email', 'tabela_afetada')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('usuario', 'acao', 'tabela_afetada', 'id_registro_afetado', 'detalhes', 'criado_em')

    def has_add_permission(self, request):
        return False


@admin.register(ConfiguracaoSistema)
class ConfiguracaoSistemaAdmin(admin.ModelAdmin):
    list_display = ('chave', 'valor', 'descricao')
    search_fields = ('chave', 'valor')
    ordering = ('chave',)


@admin.register(CodigoVerificacao)
class CodigoVerificacaoAdmin(admin.ModelAdmin):
    list_display = ('telefone', 'codigo', 'usado', 'criado_em')
    list_filter = ('usado',)
    search_fields = ('telefone',)
    ordering = ('-criado_em',)
    readonly_fields = ('telefone', 'codigo', 'usado', 'criado_em')

    def has_add_permission(self, request):
        return False
