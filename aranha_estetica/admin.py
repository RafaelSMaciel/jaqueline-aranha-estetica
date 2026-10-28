# aranha_estetica/admin.py
import json

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm
from django.db import transaction
from django.utils import timezone
# Importados antes do autodiscover p/ o unregister abaixo valer (o admin do
# django_otp registra os devices ao ser importado).
from django_otp.plugins.otp_static import admin as _otp_static_admin  # noqa: F401
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp import admin as _otp_totp_admin  # noqa: F401
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import (
    Profissional, AgendaHorario, AgendaExcecao, Habilitacao,
    Procedimento, Preco, Promocao,
    Cliente, Consentimento,
    Prontuario, AnotacaoSessao, ProntuarioVersao,
    VersaoTermo, AceiteTermo,
    Atendimento, Notificacao, LinkAcesso,
    AvaliacaoNPS,
    Pacote, ItemPacote, CompraPacote, CompraPacoteItem, ConsumoSessao,
    ListaEspera,
    LogAuditoria, Configuracao, CodigoOtp, HistoricoAlteracao,
    RegraComissao, MovimentoComissao,
    Usuario,
)
from .utils.auditoria import registrar_log
from .utils.cifragem import decifrar_profundo
from .utils.busca import filtrar_clientes


# =====================================================================
# CONTROLE DE ACESSO
# =====================================================================

# Devices de 2FA fora do Django admin: a tela do django_otp mostra semente/QR
# de qualquer usuario e o "add" planta um device com chave escolhida p/ outra
# pessoa (match_token aceita qualquer device confirmado) — um ADMIN assumiria
# a conta de outro. Cadastro/troca = tela do painel (do proprio usuario);
# reset = `manage.py setup_2fa <email> --force`.
for _modelo_otp in (TOTPDevice, StaticDevice):
    if admin.site.is_registered(_modelo_otp):
        admin.site.unregister(_modelo_otp)


class UsuarioCreationForm(BaseUserCreationForm):
    """Cadastro com senha + confirmacao; grava o HASH (set_password)."""

    class Meta:
        model = Usuario
        fields = ('email', 'nome', 'papel', 'profissional')

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if Usuario.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Já existe usuário com esse e-mail.')
        return email


class UsuarioChangeForm(UserChangeForm):
    """Senha so-leitura (hash); troca pelo form de senha proprio do admin."""

    class Meta:
        model = Usuario
        fields = ('email', 'nome', 'papel', 'profissional', 'ativo', 'password')


@admin.register(Usuario)
class UsuarioAdmin(DjangoUserAdmin):
    # O ModelForm padrao expunha `password` como texto comum e gravava a senha
    # em claro no banco. UserAdmin usa set_password + form de troca de senha.
    form = UsuarioChangeForm
    add_form = UsuarioCreationForm
    add_form_template = None
    list_display = ('email', 'nome', 'papel', 'profissional', 'ativo')
    list_filter = ('papel', 'ativo')
    search_fields = ('nome', 'email')
    ordering = ('email',)
    filter_horizontal = ()
    autocomplete_fields = ('profissional',)
    list_select_related = ('profissional',)
    list_per_page = 50
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Dados', {'fields': ('nome', 'papel', 'profissional', 'ativo')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'nome', 'papel', 'profissional', 'password1', 'password2'),
        }),
    )

    def has_delete_permission(self, request, obj=None):
        # Excluir apagaria a autoria da trilha (LogAuditoria SET_NULL; o
        # django_admin_log e CASCADE). Offboarding = desmarcar "ativo".
        return False


# =====================================================================
# PROFISSIONAIS
# =====================================================================

class AgendaHorarioInline(admin.TabularInline):
    """Grade semanal (dia ISO: 1=segunda ... 7=domingo; varias janelas por dia)."""
    model = AgendaHorario
    fields = ('dia_semana', 'hora_inicio', 'hora_fim')
    extra = 0


@admin.register(Profissional)
class ProfissionalAdmin(admin.ModelAdmin):
    list_display = ('nome', 'especialidade', 'ativo')
    list_filter = ('ativo',)
    search_fields = ('nome', 'especialidade')
    ordering = ('-ativo', 'nome')
    inlines = [AgendaHorarioInline]
    list_per_page = 50
    # tokens OAuth do Google nunca na tela (com eles se le a agenda Google da profissional)
    exclude = ('gcal_refresh_token', 'gcal_sync_token')
    readonly_fields = ('google_agenda_conectada', 'gcal_ultimo_sync_em')

    @admin.display(boolean=True, description='Google Agenda conectada')
    def google_agenda_conectada(self, obj):
        return bool(obj and obj.gcal_refresh_token)


@admin.register(AgendaHorario)
class AgendaHorarioAdmin(admin.ModelAdmin):
    list_display = ('profissional', 'dia_semana', 'hora_inicio', 'hora_fim')
    list_filter = ('profissional', 'dia_semana')
    search_fields = ('profissional__nome',)
    ordering = ('profissional', 'dia_semana', 'hora_inicio')
    autocomplete_fields = ('profissional',)
    list_select_related = ('profissional',)


@admin.register(AgendaExcecao)
class AgendaExcecaoAdmin(admin.ModelAdmin):
    """Folgas (inclusive feriados/recessos da clinica: sem profissional),
    horarios diferentes e bloqueios (pontuais ou recorrentes por RRULE).
    Desmarcar "ativo" mantem o registro e o ignora na agenda (ex.: feriado
    nacional em que a clinica abre — apagar faria o carregar_feriados recriar)."""
    list_display = ('__str__', 'tipo', 'origem', 'profissional', 'data_inicio', 'data_fim',
                    'data_hora_inicio', 'regra_recorrencia', 'ativo')
    list_filter = ('tipo', 'origem', 'ativo', 'profissional')
    search_fields = ('motivo', 'observacao', 'profissional__nome')
    ordering = ('-criado_em',)
    autocomplete_fields = ('profissional',)
    list_select_related = ('profissional',)
    list_per_page = 50
    fieldsets = (
        (None, {'fields': ('tipo', 'profissional', 'origem', 'ativo', 'motivo', 'observacao')}),
        ('Folga / horário diferente (datas locais)', {
            'fields': ('data_inicio', 'data_fim', 'hora_inicio', 'hora_fim'),
        }),
        ('Bloqueio (data e hora; recorrência opcional)', {
            'fields': ('data_hora_inicio', 'data_hora_fim', 'regra_recorrencia', 'data_fim_recorrencia'),
        }),
        ('Origem externa', {'fields': ('origem_ref',), 'classes': ('collapse',)}),
    )

    def save_model(self, request, obj, form, change):
        if not change and request.user.is_authenticated:
            obj.usuario_autor = request.user
        super().save_model(request, obj, form, change)


@admin.register(Habilitacao)
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

class ConsentimentoInline(admin.TabularInline):
    """Historico de consentimentos (append-only): so leitura no cadastro."""
    model = Consentimento
    extra = 0
    can_delete = False
    ordering = ('-registrado_em', '-id')
    fields = ('finalidade', 'canal', 'concedido', 'origem', 'registrado_em', 'usuario_autor', 'texto')
    readonly_fields = fields
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = (
        'nome', 'telefone', 'email', 'faltas_consecutivas',
        'bloqueado_online', 'ativo', 'criado_em',
    )
    list_filter = ('ativo', 'bloqueado_online')
    search_fields = ('nome', 'telefone', 'email', 'cpf')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('criado_em', 'atualizado_em', 'deletado_em')
    # Consentimentos (LGPD art. 8): historico so leitura; aqui so se revoga
    # (acao abaixo) — conceder e da propria cliente ou do painel com justificativa.
    inlines = [ConsentimentoInline]
    list_per_page = 50
    fieldsets = (
        ('Identificacao', {
            'fields': ('nome', 'data_nascimento', 'cpf', 'rg', 'profissao'),
        }),
        ('Contato', {
            'fields': ('email', 'telefone', 'cep', 'endereco'),
        }),
        ('Status', {
            'fields': ('ativo', 'faltas_consecutivas', 'bloqueado_online'),
        }),
        ('Metadados', {
            'classes': ('collapse',),
            'fields': ('criado_em', 'atualizado_em', 'deletado_em'),
        }),
    )
    actions = [
        'acao_excluir_soft', 'acao_anonimizar_lgpd', 'acao_revogar_comunicacao',
        'acao_bloquear_online', 'acao_resetar_faltas',
    ]

    def get_queryset(self, request):
        return Cliente.all_objects.get_queryset()

    def get_search_results(self, request, queryset, search_term):
        # Telefone/CPF sao gravados so com digitos: '(17) 99999-0001' e
        # '529.982.247-25' precisam da mesma busca do painel.
        if not search_term.strip():
            return super().get_search_results(request, queryset, search_term)
        return filtrar_clientes(queryset, search_term), False

    def get_actions(self, request):
        # delete_selected faz QuerySet.delete() (hard-delete em cascata),
        # contornando o soft-delete de Cliente.delete(). Fica so a versao soft.
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    @admin.action(description='Excluir selecionados (desativa — soft delete)')
    def acao_excluir_soft(self, request, queryset):
        count = 0
        for cliente in queryset:
            cliente.delete()  # soft-delete (Cliente.delete)
            count += 1
        self.message_user(request, f'{count} cliente(s) desativado(s).', messages.SUCCESS)

    @admin.action(description='Anonimizar (direito ao esquecimento LGPD)')
    def acao_anonimizar_lgpd(self, request, queryset):
        from .services import LgpdService
        count = 0
        with transaction.atomic():
            for cliente in queryset:
                LgpdService.esquecer_cliente(cliente, usuario=request.user, request=request)
                count += 1
        self.message_user(request, f'{count} cliente(s) anonimizado(s).', messages.SUCCESS)

    @admin.action(description='Revogar consentimentos de comunicação (todos os canais)')
    def acao_revogar_comunicacao(self, request, queryset):
        from .services import consentimentos
        revogados = 0
        with transaction.atomic():
            for cliente in queryset:
                novas = consentimentos.revogar_todos(
                    cliente, origem=Consentimento.ORIGEM_ADMIN,
                    texto='Revogado pela equipe no Django admin', usuario=request.user,
                )
                if novas:
                    revogados += 1
                    registrar_log(
                        request.user, 'Revogou consentimentos de comunicacao (django-admin)', 'cliente',
                        cliente.pk, detalhes={'pares': [f'{c.finalidade}:{c.canal}' for c in novas]},
                        request=request,
                    )
        self.message_user(request, f'{revogados} cliente(s) com consentimento revogado.', messages.SUCCESS)

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

class _SomenteLeituraMixin:
    """Registro que o Django admin so exibe: sem incluir, editar ou apagar."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class _LeituraClinicaAuditadaMixin(_SomenteLeituraMixin):
    """Dado de saude (LGPD art. 11): so leitura e cada abertura vai p/ a auditoria.

    Edicao so pelo painel (prontuario/anotar), que registra o autor; correcao
    de anotacao vira nova anotacao (append-only).
    """

    def _log_leitura(self, request, obj):
        raise NotImplementedError

    def change_view(self, request, object_id, form_url='', extra_context=None):
        resposta = super().change_view(request, object_id, form_url, extra_context)
        if request.method == 'GET' and resposta.status_code == 200:
            obj = self.get_object(request, unquote(object_id))
            if obj is not None:
                self._log_leitura(request, obj)
        return resposta


@admin.register(Consentimento)
class ConsentimentoAdmin(_SomenteLeituraMixin, admin.ModelAdmin):
    """Historico de consentimentos (append-only; no PG o trigger barra UPDATE/DELETE)."""
    list_display = ('cliente', 'finalidade', 'canal', 'concedido', 'origem', 'registrado_em')
    list_filter = ('finalidade', 'canal', 'concedido', 'origem')
    search_fields = ('cliente__nome', 'cliente__telefone')
    ordering = ('-registrado_em', '-id')
    date_hierarchy = 'registrado_em'
    list_select_related = ('cliente',)
    list_per_page = 50


@admin.register(Prontuario)
class ProntuarioAdmin(_LeituraClinicaAuditadaMixin, admin.ModelAdmin):
    list_display = ('cliente', 'atualizado_em')
    search_fields = ('cliente__nome',)
    ordering = ('-atualizado_em',)
    autocomplete_fields = ('cliente',)
    list_select_related = ('cliente',)

    def _log_leitura(self, request, obj):
        registrar_log(
            request.user, 'Visualizou prontuario (django-admin)', 'prontuario', obj.cliente_id,
            request=request,
        )


@admin.register(ProntuarioVersao)
class ProntuarioVersaoAdmin(_LeituraClinicaAuditadaMixin, admin.ModelAdmin):
    """Historico append-only do prontuario (foto de dado de saude anterior a
    cada edicao): so leitura, com a mesma trilha de leitura do Prontuario."""
    list_display = ('prontuario', 'autor_nome', 'criado_em')
    search_fields = ('prontuario__cliente__nome',)
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    list_select_related = ('prontuario', 'prontuario__cliente')

    def _log_leitura(self, request, obj):
        registrar_log(
            request.user, 'Visualizou versao do prontuario (django-admin)', 'prontuario',
            obj.prontuario.cliente_id, detalhes={'prontuario_versao_id': obj.pk}, request=request,
        )


@admin.register(HistoricoAlteracao)
class HistoricoAlteracaoAdmin(_LeituraClinicaAuditadaMixin, admin.ModelAdmin):
    """Trilha de dados (antes/depois) gravada por trigger no Postgres (spec padrao-sql §5.3).

    Inclui dado de saude (prontuario, retencao 20 anos): so leitura, e cada
    abertura vai p/ a auditoria (como Prontuario/ProntuarioVersao). O Django
    admin ja e restrito a ADMIN (is_staff).
    """
    list_display = ('criado_em', 'tabela', 'registro_pk', 'operacao', 'usuario_autor', 'origem', 'ip_origem')
    list_filter = ('tabela', 'operacao', 'origem')
    search_fields = ('registro_pk__exact', 'transacao_ref__exact')  # Cast p/ texto: termo nao numerico nao quebra
    ordering = ('-criado_em', '-id')
    date_hierarchy = 'criado_em'
    list_select_related = ('usuario_autor',)
    list_per_page = 50
    # no banco o dado de saude da trilha do prontuario esta cifrado (0060): a tela mostra decifrado
    exclude = ('dados_antes', 'dados_depois')
    readonly_fields = ('dados_antes_legiveis', 'dados_depois_legiveis')

    @staticmethod
    def _legivel(dados):
        if dados is None:
            return '-'
        return json.dumps(decifrar_profundo(dados), ensure_ascii=False, indent=2, sort_keys=True)

    @admin.display(description='Dados antes')
    def dados_antes_legiveis(self, obj):
        return self._legivel(obj.dados_antes)

    @admin.display(description='Dados depois')
    def dados_depois_legiveis(self, obj):
        return self._legivel(obj.dados_depois)

    def _log_leitura(self, request, obj):
        registrar_log(
            request.user, 'Visualizou historico de alteracao (django-admin)', 'historico_alteracao', obj.pk,
            detalhes={'tabela': obj.tabela, 'registro_pk': obj.registro_pk}, request=request,
        )


@admin.register(AnotacaoSessao)
class AnotacaoSessaoAdmin(_LeituraClinicaAuditadaMixin, admin.ModelAdmin):
    list_display = ('atendimento', 'autor', 'criado_em')
    search_fields = ('atendimento__cliente__nome',)  # texto e cifrado em repouso (0060): sem busca
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    autocomplete_fields = ('atendimento', 'autor')
    list_select_related = ('atendimento', 'atendimento__cliente', 'autor')

    def _log_leitura(self, request, obj):
        registrar_log(
            request.user, 'Visualizou anotacao de sessao (django-admin)', 'anotacao_sessao', obj.pk,
            detalhes={'atendimento_id': obj.atendimento_id}, request=request,
        )


# =====================================================================
# TERMOS DE CONSENTIMENTO
# =====================================================================

def _versao_tem_aceites(obj) -> bool:
    return (
        obj is not None and obj.pk is not None
        and AceiteTermo.objects.filter(versao_termo=obj).exists()
    )


class VersaoTermoAdminForm(forms.ModelForm):
    class Meta:
        model = VersaoTermo
        fields = '__all__'

    def clean(self):
        dados = super().clean()
        # Com tipo/procedimento so-leitura (versao ja aceita) o Django pula a
        # UniqueConstraint (1 ativa por escopo) e reativar daria IntegrityError/500.
        if 'tipo' in self.fields:
            return dados  # campos editaveis: o validate_constraints do Django cobre
        inst = self.instance
        if dados.get('ativa') and inst.tipo:
            outras = VersaoTermo.objects.filter(
                tipo=inst.tipo, procedimento=inst.procedimento, ativa=True,
            ).exclude(pk=inst.pk)
            if outras.exists():
                raise forms.ValidationError(
                    'Já existe uma versão ativa deste termo. Desative-a antes de ativar esta.'
                )
        return dados


@admin.register(VersaoTermo)
class VersaoTermoAdmin(admin.ModelAdmin):
    """Versao ja aceita e prova do que a cliente aceitou (LGPD art. 8 §2).

    Com aceites o texto fica congelado (so da para desativar); corrigir o
    texto = publicar uma versao nova.
    """
    form = VersaoTermoAdminForm
    list_display = ('titulo', 'tipo', 'versao', 'procedimento', 'vigente_desde', 'ativa')
    list_filter = ('tipo', 'ativa')
    search_fields = ('titulo', 'procedimento__nome', 'versao')
    ordering = ('-ativa', '-vigente_desde')
    autocomplete_fields = ('procedimento',)
    list_select_related = ('procedimento',)

    def get_readonly_fields(self, request, obj=None):
        campos = tuple(super().get_readonly_fields(request, obj))
        if not _versao_tem_aceites(obj):
            return campos
        congelados = tuple(
            f.name for f in self.model._meta.fields
            if f.editable and not f.primary_key and f.name != 'ativa' and f.name not in campos
        )
        return campos + congelados

    def has_delete_permission(self, request, obj=None):
        if _versao_tem_aceites(obj):
            return False
        return super().has_delete_permission(request, obj)


@admin.register(AceiteTermo)
class AceiteTermoAdmin(_SomenteLeituraMixin, admin.ModelAdmin):
    """Unificado (fase 6): LGPD e procedimento — tipo via versao_termo.

    Evidencia legal (retencao longa): so o fluxo de aceite grava; o admin
    apenas consulta (nada de fabricar/alterar aceite, IP ou user-agent).
    """
    list_display = ('cliente', 'versao_termo', 'atendimento', 'ip', 'criado_em')
    list_filter = ('versao_termo__tipo',)
    search_fields = ('cliente__nome', 'versao_termo__titulo')
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
    readonly_fields = ('tipo', 'canal', 'status', 'resposta', 'enviado_em', 'criado_em')
    can_delete = False


@admin.register(Atendimento)
class AtendimentoAdmin(admin.ModelAdmin):
    list_display = (
        'data_hora_inicio', 'cliente', 'profissional', 'procedimento',
        'status', 'valor_cobrado',
    )
    list_filter = ('status', 'profissional', 'procedimento')
    search_fields = ('cliente__nome', 'cliente__telefone', 'profissional__nome')
    ordering = ('-data_hora_inicio',)
    date_hierarchy = 'data_hora_inicio'
    readonly_fields = ('criado_em', 'atualizado_em')
    autocomplete_fields = ('cliente', 'profissional', 'procedimento', 'promocao', 'reagendado_de')
    list_select_related = ('cliente', 'profissional', 'procedimento')
    list_per_page = 50
    inlines = [NotificacaoInline]
    actions = ['acao_marcar_realizado', 'acao_marcar_cancelado', 'acao_marcar_faltou']

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            from .services.atendimento import ao_criar
            ao_criar(obj)   # push + Google Calendar, como nas outras portas (services.agenda)

    def get_readonly_fields(self, request, obj=None):
        # Status so muda pela FSM (actions abaixo / painel), nunca editando o
        # campo — nem no "add": nasceria REALIZADO sem comissao/cashback/
        # retorno/NPS. O novo usa o default do model (PENDENTE).
        return (*super().get_readonly_fields(request, obj), 'status')

    def _transicionar(self, request, queryset, metodo, rotulo, sem_termo=0, apos=None, **kwargs):
        ok = ignorados = 0
        for at in queryset:
            try:
                getattr(at, metodo)(by_user=request.user, **kwargs)
                ok += 1
            except Atendimento.TransicaoInvalida:
                ignorados += 1
                continue
            if apos is not None:
                apos(at)
        msg = f'{ok} atendimento(s) {rotulo}.'
        if ignorados:
            msg += f' {ignorados} ignorado(s): transição de status não permitida.'
        if sem_termo:
            msg += (
                f' {sem_termo} ignorado(s): a cliente ainda não aceitou o termo do '
                'procedimento (marque pelo painel, que pede confirmação e registra na auditoria).'
            )
        aviso = ignorados or sem_termo
        self.message_user(request, msg, messages.WARNING if aviso else messages.SUCCESS)

    @admin.action(description='Marcar selecionados como REALIZADO')
    def acao_marcar_realizado(self, request, queryset):
        # Termo de PROCEDIMENTO pendente: painel e portal exigem confirmacao
        # explicita e gravam 'Realizado sem termo aceito'; a action nao tem como
        # confirmar, entao pula esses (nunca realiza em silencio).
        from .services.termos import ids_com_termo_procedimento_pendente
        atendimentos = list(queryset)
        pendentes = ids_com_termo_procedimento_pendente(atendimentos)
        self._transicionar(
            request, [at for at in atendimentos if at.pk not in pendentes],
            'marcar_realizado', 'marcado(s) como realizado', sem_termo=len(pendentes),
        )

    @admin.action(description='Cancelar selecionados')
    def acao_marcar_cancelado(self, request, queryset):
        from .services.agendamento import avisar_cancelamento
        # como o cancelamento pelo painel: a cliente recebe o e-mail
        self._transicionar(
            request, queryset, 'cancelar', 'cancelado(s)', motivo='Cancelado via Django admin',
            apos=avisar_cancelamento,
        )

    @admin.action(description='Marcar como FALTOU')
    def acao_marcar_faltou(self, request, queryset):
        self._transicionar(request, queryset, 'marcar_falta', 'marcado(s) como falta')


@admin.register(Notificacao)
class NotificacaoAdmin(admin.ModelAdmin):
    list_display = ('atendimento', 'tipo', 'canal', 'status', 'resposta', 'enviado_em', 'criado_em')
    list_filter = ('tipo', 'canal', 'status', 'resposta')
    search_fields = ('atendimento__cliente__nome', 'mensagem')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('criado_em',)
    autocomplete_fields = ('atendimento',)
    list_select_related = ('atendimento', 'atendimento__cliente')
    list_per_page = 50


@admin.register(LinkAcesso)
class LinkAcessoAdmin(_SomenteLeituraMixin, admin.ModelAdmin):
    """Links publicos (so o hash do token fica no banco — nada a exibir de secreto).

    Somente leitura; a unica acao e revogar (o link passa a dar 404).
    """
    list_display = ('finalidade', 'alvo_id_rotulo', 'criado_em', 'expira_em', 'usado_em', 'revogado_em')
    list_filter = ('finalidade',)
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    exclude = ('token_hash',)
    list_per_page = 50
    actions = ['acao_revogar']

    @admin.action(description='Revogar os links selecionados')
    def acao_revogar(self, request, queryset):
        qtd = queryset.filter(revogado_em__isnull=True).update(revogado_em=timezone.now())
        registrar_log(
            request.user, f'Revogou {qtd} link(s) publico(s) pelo admin', 'link_acesso', None,
            detalhes={'ids': list(queryset.values_list('pk', flat=True))[:200]}, request=request,
        )
        self.message_user(request, f'{qtd} link(s) revogado(s).', messages.SUCCESS)


# =====================================================================
# AVALIACAO NPS
# =====================================================================

@admin.register(AvaliacaoNPS)
class AvaliacaoNPSAdmin(admin.ModelAdmin):
    list_display = (
        'atendimento', 'nota', 'alerta_enviado',
        'autoriza_publicacao', 'aprovado_publicacao', 'criado_em',
    )
    list_filter = ('nota', 'alerta_enviado', 'autoriza_publicacao', 'aprovado_publicacao')
    search_fields = ('atendimento__cliente__nome', 'comentario')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    # Nota, comentario e opt-in de publicacao sao da cliente (LGPD): a equipe
    # so modera (aprovado_publicacao). A pagina publica exige os dois flags.
    readonly_fields = (
        'atendimento', 'nota', 'comentario', 'autoriza_publicacao', 'alerta_enviado', 'criado_em',
    )
    list_select_related = ('atendimento', 'atendimento__cliente')

    def has_add_permission(self, request):
        # Avaliacao so nasce da propria cliente (link de NPS)
        return False


# =====================================================================
# PACOTES
# =====================================================================

class ItemPacoteInline(admin.TabularInline):
    """Itens do CATALOGO: editaveis sempre (a venda congela os itens em
    CompraPacoteItem; mudar aqui so afeta vendas futuras)."""
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


class CompraPacoteItemInline(admin.TabularInline):
    """O que foi vendido (snapshot imutavel da venda) — so leitura."""
    model = CompraPacoteItem
    extra = 0
    can_delete = False
    fields = ('procedimento_nome', 'quantidade_sessoes', 'valor_unitario', 'criado_em')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CompraPacote)
class PacoteClienteAdmin(_SomenteLeituraMixin, admin.ModelAdmin):
    """So consulta: venda, cancelamento/reembolso e validade mudam pelo painel (com motivo e
    auditoria). Editar status/reembolso aqui furava a regra do pacote sem rastro."""
    list_display = ('cliente', 'pacote_nome', 'status', 'valor_pago', 'valor_reembolsado', 'data_expiracao', 'criado_em')
    list_filter = ('status',)
    search_fields = ('cliente__nome', 'pacote_nome')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('cliente', 'pacote', 'pacote_nome', 'valor_pago', 'criado_em')
    list_select_related = ('cliente',)
    inlines = [CompraPacoteItemInline]


@admin.register(ConsumoSessao)
class SessaoPacoteAdmin(_SomenteLeituraMixin, admin.ModelAdmin):
    """Gerado pelo debito do atendimento REALIZADO (services/atendimento). So consulta: excluir
    aqui devolvia a sessao e inflava o faturamento com o preco cheio."""
    list_display = ('compra_pacote', 'compra_pacote_item', 'atendimento', 'criado_em')
    search_fields = (
        'compra_pacote__cliente__nome',
        'compra_pacote__pacote_nome',
    )
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('compra_pacote', 'compra_pacote_item', 'atendimento', 'criado_em')
    list_select_related = ('compra_pacote', 'compra_pacote_item', 'atendimento', 'compra_pacote__cliente')


# =====================================================================
# LISTA DE ESPERA
# =====================================================================

@admin.register(ListaEspera)
class ListaEsperaAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'procedimento', 'profissional_desejado', 'data_desejada', 'turno_desejado', 'notificado', 'criado_em')
    list_filter = ('notificado', 'turno_desejado')
    search_fields = ('cliente__nome', 'procedimento__nome')
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
    list_display = ('criado_em', 'usuario', 'acao', 'tabela', 'registro_id', 'ip_origem')
    list_filter = ('tabela', UltimosDiasFilter)
    search_fields = ('acao', 'usuario__email', 'usuario_nome', 'tabela')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = (
        'usuario', 'usuario_nome', 'acao', 'tabela', 'registro_id',
        'detalhes', 'ip_origem', 'criado_em',
    )
    list_select_related = ('usuario',)
    list_per_page = 100

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Trilha de auditoria e imutavel (nem o admin apaga)
        return False


@admin.register(Configuracao)
class ConfiguracaoSistemaAdmin(admin.ModelAdmin):
    list_display = ('chave', 'valor', 'descricao')
    search_fields = ('chave', 'valor')
    ordering = ('chave',)


@admin.register(CodigoOtp)
class OtpCodeAdmin(admin.ModelAdmin):
    """Read-only: codigo e hashed — admin ve apenas metadados do challenge."""
    list_display = ('email', 'telefone', 'proposito', 'canal', 'tentativas', 'usado_em', 'expira_em', 'criado_em')
    list_filter = ('proposito', 'canal')
    search_fields = ('email', 'telefone')
    ordering = ('-criado_em',)
    readonly_fields = (
        'email', 'telefone', 'canal', 'codigo_hash', 'proposito',
        'tentativas', 'usado_em', 'expira_em', 'ip_origem', 'criado_em',
    )

    def has_add_permission(self, request):
        return False


# =====================================================================
# COMISSOES E CALENDARIO
# =====================================================================

@admin.register(RegraComissao)
class RegraComissaoAdmin(admin.ModelAdmin):
    """Tela principal: painel > Comissoes > Regras. Sem regra ativa nenhuma comissao e gerada.

    Percentual OU valor fixo (CHECK no banco). Profissional/procedimento vazios
    = vale para todos; a regra mais especifica vence. Regra ja usada em comissao
    nao muda nem se exclui (o credito cita a regra): aqui so o "ativo" fica editavel.
    """
    list_display = ('__str__', 'profissional', 'procedimento', 'percentual', 'valor', 'ativo')
    list_filter = ('ativo', 'profissional')
    search_fields = ('profissional__nome', 'procedimento__nome')
    autocomplete_fields = ('profissional', 'procedimento')
    list_select_related = ('profissional', 'procedimento')

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.foi_usada():
            return ('profissional', 'procedimento', 'percentual', 'valor')
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.foi_usada():
            return False
        return super().has_delete_permission(request, obj)


@admin.register(MovimentoComissao)
class MovimentoComissaoAdmin(admin.ModelAdmin):
    """Somente leitura (livro-razao append-only): CREDITO gerado pelo servico,
    PAGAMENTO pela tela Comissoes do painel, ESTORNO no cancelamento."""
    list_display = ('criado_em', 'tipo', 'profissional', 'atendimento', 'valor', 'movimento_origem',
                    'usuario_autor')
    list_filter = ('tipo', 'profissional')
    search_fields = ('profissional__nome', 'atendimento__cliente__nome')
    ordering = ('-criado_em', '-pk')
    date_hierarchy = 'criado_em'
    list_select_related = ('profissional', 'atendimento', 'atendimento__cliente', 'usuario_autor')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# Feriados/recessos: AgendaExcecaoAdmin (FOLGA sem profissional, origem FERIADO_*/RECESSO).
