# app_shivazen/admin.py
from django.contrib import admin, messages
from django.utils import timezone

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
    list_per_page = 50


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
    list_select_related = ('perfil', 'funcionalidade')


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('email', 'nome', 'perfil', 'profissional', 'ativo')
    list_filter = ('perfil', 'ativo')
    search_fields = ('nome', 'email')
    ordering = ('email',)
    autocomplete_fields = ('perfil', 'profissional')
    list_select_related = ('perfil', 'profissional')
    list_per_page = 50


# =====================================================================
# PROFISSIONAIS
# =====================================================================

class DisponibilidadeInline(admin.TabularInline):
    model = DisponibilidadeProfissional
    extra = 0


@admin.register(Profissional)
class ProfissionalAdmin(admin.ModelAdmin):
    list_display = ('nome', 'especialidade', 'ativo')
    list_filter = ('ativo',)
    search_fields = ('nome', 'especialidade')
    ordering = ('-ativo', 'nome')
    inlines = [DisponibilidadeInline]
    list_per_page = 50


@admin.register(DisponibilidadeProfissional)
class DisponibilidadeProfissionalAdmin(admin.ModelAdmin):
    list_display = ('profissional', 'dia_semana', 'hora_inicio', 'hora_fim')
    list_filter = ('profissional', 'dia_semana')
    search_fields = ('profissional__nome',)
    ordering = ('profissional', 'dia_semana', 'hora_inicio')
    autocomplete_fields = ('profissional',)
    list_select_related = ('profissional',)


@admin.register(BloqueioAgenda)
class BloqueioAgendaAdmin(admin.ModelAdmin):
    list_display = ('profissional', 'data_hora_inicio', 'data_hora_fim', 'motivo')
    list_filter = ('profissional',)
    search_fields = ('profissional__nome', 'motivo')
    ordering = ('-data_hora_inicio',)
    date_hierarchy = 'data_hora_inicio'
    autocomplete_fields = ('profissional',)
    list_select_related = ('profissional',)


@admin.register(ProfissionalProcedimento)
class ProfissionalProcedimentoAdmin(admin.ModelAdmin):
    list_display = ('profissional', 'procedimento')
    list_filter = ('profissional',)
    search_fields = ('profissional__nome', 'procedimento__nome')
    autocomplete_fields = ('profissional', 'procedimento')
    list_select_related = ('profissional', 'procedimento')


# =====================================================================
# PROCEDIMENTOS E PRECOS
# =====================================================================

@admin.register(Procedimento)
class ProcedimentoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'categoria', 'duracao_minutos', 'ativo')
    list_filter = ('categoria', 'ativo')
    search_fields = ('nome', 'descricao')
    prepopulated_fields = {'slug': ('nome',)}
    ordering = ('-ativo', 'categoria', 'nome')
    list_per_page = 50


@admin.register(Preco)
class PrecoAdmin(admin.ModelAdmin):
    list_display = ('procedimento', 'profissional', 'valor', 'vigente_desde')
    list_filter = ('profissional', 'vigente_desde')
    search_fields = ('procedimento__nome', 'profissional__nome')
    ordering = ('-vigente_desde', 'procedimento')
    autocomplete_fields = ('procedimento', 'profissional')
    list_select_related = ('procedimento', 'profissional')


@admin.register(Promocao)
class PromocaoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'procedimento', 'desconto_percentual', 'data_inicio', 'data_fim', 'ativa')
    list_filter = ('ativa', 'data_inicio')
    search_fields = ('nome', 'procedimento__nome')
    ordering = ('-ativa', '-data_inicio')
    date_hierarchy = 'data_inicio'
    autocomplete_fields = ('procedimento',)
    list_select_related = ('procedimento',)


# =====================================================================
# CLIENTES
# =====================================================================

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = (
        'nome_completo', 'telefone', 'email', 'faltas_consecutivas',
        'bloqueado_online', 'ativo', 'aceita_comunicacao', 'criado_em',
    )
    list_filter = ('ativo', 'bloqueado_online', 'aceita_comunicacao')
    search_fields = ('nome_completo', 'telefone', 'email', 'cpf')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('criado_em', 'atualizado_em', 'deletado_em', 'unsubscribe_token')
    list_per_page = 50
    fieldsets = (
        ('Identificacao', {
            'fields': ('nome_completo', 'data_nascimento', 'cpf', 'rg', 'profissao'),
        }),
        ('Contato', {
            'fields': ('email', 'telefone', 'cep', 'endereco'),
        }),
        ('Status e LGPD', {
            'fields': (
                'ativo', 'aceita_comunicacao', 'unsubscribe_token',
                'faltas_consecutivas', 'bloqueado_online',
            ),
        }),
        ('Consents granulares (LGPD)', {
            'fields': (
                'consent_email_marketing', 'consent_email_marketing_at', 'consent_email_marketing_ip',
                'consent_whatsapp_nps', 'consent_whatsapp_nps_at', 'consent_whatsapp_nps_ip',
            ),
        }),
        ('Metadados', {
            'classes': ('collapse',),
            'fields': ('criado_em', 'atualizado_em', 'deletado_em'),
        }),
    )
    actions = ['acao_anonimizar_lgpd', 'acao_bloquear_online', 'acao_resetar_faltas']

    def get_queryset(self, request):
        return Cliente.all_objects.get_queryset()

    @admin.action(description='Anonimizar (direito ao esquecimento LGPD)')
    def acao_anonimizar_lgpd(self, request, queryset):
        from .services import LgpdService
        count = 0
        for cliente in queryset:
            LgpdService.esquecer_cliente(cliente)
            count += 1
        self.message_user(request, f'{count} cliente(s) anonimizado(s).', messages.SUCCESS)

    @admin.action(description='Bloquear agendamento online')
    def acao_bloquear_online(self, request, queryset):
        count = queryset.update(bloqueado_online=True)
        self.message_user(request, f'{count} cliente(s) bloqueado(s).')

    @admin.action(description='Resetar contador de faltas')
    def acao_resetar_faltas(self, request, queryset):
        count = queryset.update(faltas_consecutivas=0, bloqueado_online=False)
        self.message_user(request, f'{count} cliente(s) com faltas resetadas.')


# =====================================================================
# PRONTUARIO
# =====================================================================

@admin.register(Prontuario)
class ProntuarioAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'atualizado_em')
    search_fields = ('cliente__nome_completo',)
    ordering = ('-atualizado_em',)
    autocomplete_fields = ('cliente',)
    list_select_related = ('cliente',)


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
    list_select_related = ('prontuario', 'pergunta', 'prontuario__cliente')


@admin.register(AnotacaoSessao)
class AnotacaoSessaoAdmin(admin.ModelAdmin):
    list_display = ('atendimento', 'usuario', 'criado_em')
    search_fields = ('atendimento__cliente__nome_completo', 'texto')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    autocomplete_fields = ('atendimento', 'usuario')
    list_select_related = ('atendimento', 'atendimento__cliente', 'usuario')


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
    list_select_related = ('procedimento',)


@admin.register(AceitePrivacidade)
class AceitePrivacidadeAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'versao_termo', 'ip', 'criado_em')
    search_fields = ('cliente__nome_completo', 'versao_termo__titulo')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    autocomplete_fields = ('cliente', 'versao_termo')
    list_select_related = ('cliente', 'versao_termo')


@admin.register(AssinaturaTermoProcedimento)
class AssinaturaTermoProcedimentoAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'versao_termo', 'atendimento', 'ip', 'criado_em')
    search_fields = ('cliente__nome_completo', 'versao_termo__titulo')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    autocomplete_fields = ('cliente', 'versao_termo', 'atendimento')
    list_select_related = ('cliente', 'versao_termo', 'atendimento')


# =====================================================================
# AGENDAMENTO
# =====================================================================

class NotificacaoInline(admin.TabularInline):
    model = Notificacao
    extra = 0
    readonly_fields = ('tipo', 'canal', 'status_envio', 'resposta_cliente', 'enviado_em', 'criado_em')
    can_delete = False


@admin.register(Atendimento)
class AtendimentoAdmin(admin.ModelAdmin):
    list_display = (
        'data_hora_inicio', 'cliente', 'profissional', 'procedimento',
        'status', 'valor_cobrado',
    )
    list_filter = ('status', 'profissional', 'procedimento')
    search_fields = ('cliente__nome_completo', 'cliente__telefone', 'profissional__nome')
    ordering = ('-data_hora_inicio',)
    date_hierarchy = 'data_hora_inicio'
    readonly_fields = ('criado_em', 'atualizado_em', 'token_cancelamento')
    autocomplete_fields = ('cliente', 'profissional', 'procedimento', 'promocao', 'reagendado_de')
    list_select_related = ('cliente', 'profissional', 'procedimento')
    list_per_page = 50
    inlines = [NotificacaoInline]
    actions = ['acao_marcar_realizado', 'acao_marcar_cancelado', 'acao_marcar_faltou']

    @admin.action(description='Marcar selecionados como REALIZADO')
    def acao_marcar_realizado(self, request, queryset):
        count = 0
        for at in queryset.exclude(status__in=['REALIZADO', 'CANCELADO']):
            at.status = 'REALIZADO'
            at.save(update_fields=['status', 'atualizado_em'])
            count += 1
        self.message_user(request, f'{count} atendimento(s) marcado(s) como realizado.')

    @admin.action(description='Cancelar selecionados')
    def acao_marcar_cancelado(self, request, queryset):
        count = 0
        for at in queryset.exclude(status__in=['REALIZADO', 'CANCELADO']):
            at.status = 'CANCELADO'
            at.save(update_fields=['status', 'atualizado_em'])
            count += 1
        self.message_user(request, f'{count} atendimento(s) cancelado(s).')

    @admin.action(description='Marcar como FALTOU')
    def acao_marcar_faltou(self, request, queryset):
        count = 0
        for at in queryset.exclude(status__in=['REALIZADO', 'CANCELADO', 'FALTOU']):
            at.status = 'FALTOU'
            at.save(update_fields=['status', 'atualizado_em'])
            count += 1
        self.message_user(request, f'{count} atendimento(s) marcado(s) como faltou.')


@admin.register(Notificacao)
class NotificacaoAdmin(admin.ModelAdmin):
    list_display = ('atendimento', 'tipo', 'canal', 'status_envio', 'resposta_cliente', 'enviado_em', 'criado_em')
    list_filter = ('tipo', 'canal', 'status_envio', 'resposta_cliente')
    search_fields = ('atendimento__cliente__nome_completo', 'mensagem')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('token', 'criado_em')
    autocomplete_fields = ('atendimento',)
    list_select_related = ('atendimento', 'atendimento__cliente')
    list_per_page = 50


# =====================================================================
# AVALIACAO NPS
# =====================================================================

@admin.register(AvaliacaoNPS)
class AvaliacaoNPSAdmin(admin.ModelAdmin):
    list_display = ('atendimento', 'nota', 'alerta_enviado', 'criado_em')
    list_filter = ('nota', 'alerta_enviado')
    search_fields = ('atendimento__cliente__nome_completo', 'comentario')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('criado_em',)
    autocomplete_fields = ('atendimento',)
    list_select_related = ('atendimento', 'atendimento__cliente')


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
    list_select_related = ('pacote', 'procedimento')


@admin.register(PacoteCliente)
class PacoteClienteAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'pacote', 'status', 'valor_pago', 'data_expiracao', 'criado_em')
    list_filter = ('status',)
    search_fields = ('cliente__nome_completo', 'pacote__nome')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('criado_em',)
    autocomplete_fields = ('cliente', 'pacote')
    list_select_related = ('cliente', 'pacote')


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
    list_select_related = ('pacote_cliente', 'atendimento', 'pacote_cliente__cliente')


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
    list_select_related = ('cliente', 'procedimento', 'profissional_desejado')


# =====================================================================
# AUDITORIA E SISTEMA
# =====================================================================

class UltimosDiasFilter(admin.SimpleListFilter):
    title = 'Periodo'
    parameter_name = 'periodo'

    def lookups(self, request, model_admin):
        return (
            ('7', 'Ultimos 7 dias'),
            ('30', 'Ultimos 30 dias'),
            ('90', 'Ultimos 90 dias'),
        )

    def queryset(self, request, queryset):
        if self.value():
            from datetime import timedelta
            dias = int(self.value())
            limite = timezone.now() - timedelta(days=dias)
            return queryset.filter(criado_em__gte=limite)
        return queryset


@admin.register(LogAuditoria)
class LogAuditoriaAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'usuario', 'acao', 'tabela_afetada', 'id_registro_afetado', 'ip_origem')
    list_filter = ('tabela_afetada', UltimosDiasFilter)
    search_fields = ('acao', 'usuario__email', 'tabela_afetada')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = (
        'usuario', 'acao', 'tabela_afetada', 'id_registro_afetado',
        'detalhes', 'ip_origem', 'criado_em',
    )
    list_select_related = ('usuario',)
    list_per_page = 100

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
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
