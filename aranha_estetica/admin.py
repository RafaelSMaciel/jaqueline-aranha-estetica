# aranha_estetica/admin.py
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
    Profissional, DisponibilidadeProfissional, BloqueioAgenda, Habilitacao,
    Procedimento, Preco, Promocao,
    Cliente,
    Prontuario, AnotacaoSessao,
    VersaoTermo, AceiteTermo,
    Atendimento, Notificacao,
    AvaliacaoNPS,
    Pacote, ItemPacote, CompraPacote, ConsumoSessao,
    ListaEspera,
    LogAuditoria, Configuracao, CodigoOtp, Feriado,
    RegraComissao, MovimentoComissao,
    Usuario,
)
from .utils.audit import registrar_log
from .utils.busca import q_busca_cliente


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

_CONSENTS = ('consent_email_marketing', 'consent_whatsapp_nps', 'consent_whatsapp_confirmacao')


class ClienteAdminForm(forms.ModelForm):
    """Consentimento e prova do titular (LGPD art. 8): aqui so se revoga."""

    class Meta:
        model = Cliente
        fields = '__all__'

    def clean(self):
        dados = super().clean()
        for campo in _CONSENTS:
            if dados.get(campo) and not getattr(self.instance, campo, False):
                self.add_error(
                    campo,
                    'Consentimento só pode ser dado pela própria cliente (agendamento ou link). '
                    'Aqui é possível apenas revogar.',
                )
        return dados


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    form = ClienteAdminForm
    list_display = (
        'nome', 'telefone', 'email', 'faltas_consecutivas',
        'bloqueado_online', 'ativo', 'aceita_comunicacao', 'criado_em',
    )
    list_filter = ('ativo', 'bloqueado_online', 'aceita_comunicacao')
    search_fields = ('nome', 'telefone', 'email', 'cpf')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    # Data/IP do consentimento sao evidencia gravada no aceite: nunca editaveis.
    readonly_fields = (
        'criado_em', 'atualizado_em', 'deletado_em', 'token_descadastro',
        'consent_email_marketing_em', 'consent_email_marketing_ip',
        'consent_whatsapp_nps_em', 'consent_whatsapp_nps_ip',
        'consent_whatsapp_confirmacao_em', 'consent_whatsapp_confirmacao_ip',
    )
    list_per_page = 50
    fieldsets = (
        ('Identificacao', {
            'fields': ('nome', 'data_nascimento', 'cpf', 'rg', 'profissao'),
        }),
        ('Contato', {
            'fields': ('email', 'telefone', 'cep', 'endereco'),
        }),
        ('Status e LGPD', {
            'fields': (
                'ativo', 'aceita_comunicacao', 'token_descadastro',
                'faltas_consecutivas', 'bloqueado_online',
            ),
        }),
        ('Consents granulares (LGPD)', {
            'fields': (
                'consent_email_marketing', 'consent_email_marketing_em', 'consent_email_marketing_ip',
                'consent_whatsapp_nps', 'consent_whatsapp_nps_em', 'consent_whatsapp_nps_ip',
                'consent_whatsapp_confirmacao', 'consent_whatsapp_confirmacao_em',
                'consent_whatsapp_confirmacao_ip',
            ),
        }),
        ('Metadados', {
            'classes': ('collapse',),
            'fields': ('criado_em', 'atualizado_em', 'deletado_em'),
        }),
    )
    actions = [
        'acao_excluir_soft', 'acao_anonimizar_lgpd', 'acao_bloquear_online', 'acao_resetar_faltas',
    ]

    def get_queryset(self, request):
        return Cliente.all_objects.get_queryset()

    def get_search_results(self, request, queryset, search_term):
        # Telefone/CPF sao gravados so com digitos: '(17) 99999-0001' e
        # '529.982.247-25' precisam da mesma busca do painel.
        if not search_term.strip():
            return super().get_search_results(request, queryset, search_term)
        return queryset.filter(q_busca_cliente(search_term)), False

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


@admin.register(AnotacaoSessao)
class AnotacaoSessaoAdmin(_LeituraClinicaAuditadaMixin, admin.ModelAdmin):
    list_display = ('atendimento', 'autor', 'criado_em')
    search_fields = ('atendimento__cliente__nome', 'texto')
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
    readonly_fields = ('criado_em', 'atualizado_em', 'token_cancelamento')
    autocomplete_fields = ('cliente', 'profissional', 'procedimento', 'promocao', 'reagendado_de')
    list_select_related = ('cliente', 'profissional', 'procedimento')
    list_per_page = 50
    inlines = [NotificacaoInline]
    actions = ['acao_marcar_realizado', 'acao_marcar_cancelado', 'acao_marcar_faltou']

    def get_readonly_fields(self, request, obj=None):
        # Status so muda pela FSM (actions abaixo / painel), nunca editando o
        # campo — nem no "add": nasceria REALIZADO sem comissao/cashback/
        # retorno/NPS. O novo usa o default do model (PENDENTE).
        return (*super().get_readonly_fields(request, obj), 'status')

    def _transicionar(self, request, queryset, metodo, rotulo, sem_termo=0, **kwargs):
        ok = ignorados = 0
        for at in queryset:
            try:
                getattr(at, metodo)(by_user=request.user, **kwargs)
                ok += 1
            except Atendimento.TransicaoInvalida:
                ignorados += 1
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
        self._transicionar(
            request, queryset, 'cancelar', 'cancelado(s)', motivo='Cancelado via Django admin',
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
    readonly_fields = ('token', 'criado_em')
    autocomplete_fields = ('atendimento',)
    list_select_related = ('atendimento', 'atendimento__cliente')
    list_per_page = 50


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

def _pacote_vendido(pacote) -> bool:
    """Pacote com CompraPacote: nome e itens congelados (mesma regra do painel).

    CompraPacote nao guarda copia dos itens; saldo, debito e comissao leem
    pacote.itens na hora — mudar os itens alteraria o que a cliente comprou.
    """
    return bool(
        pacote is not None and pacote.pk
        and CompraPacote.objects.filter(pacote=pacote).exists()
    )


class ItemPacoteInline(admin.TabularInline):
    model = ItemPacote
    extra = 1
    autocomplete_fields = ('procedimento',)

    # obj = Pacote (pai)
    def has_add_permission(self, request, obj=None):
        return not _pacote_vendido(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not _pacote_vendido(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not _pacote_vendido(obj) and super().has_delete_permission(request, obj)


@admin.register(Pacote)
class PacoteAdmin(admin.ModelAdmin):
    list_display = ('nome', 'preco_total', 'validade_meses', 'ativo')
    list_filter = ('ativo',)
    search_fields = ('nome', 'descricao')
    ordering = ('-ativo', 'nome')
    inlines = [ItemPacoteInline]

    def get_readonly_fields(self, request, obj=None):
        campos = tuple(super().get_readonly_fields(request, obj))
        return (*campos, 'nome') if _pacote_vendido(obj) else campos


class ItemPacoteAdminForm(forms.ModelForm):
    class Meta:
        model = ItemPacote
        fields = '__all__'

    def clean_pacote(self):
        pacote = self.cleaned_data.get('pacote')
        if _pacote_vendido(pacote):
            raise forms.ValidationError(
                'Pacote já vendido: os itens não podem mudar. Crie um pacote novo.'
            )
        return pacote


@admin.register(ItemPacote)
class ItemPacoteAdmin(admin.ModelAdmin):
    form = ItemPacoteAdminForm
    list_display = ('pacote', 'procedimento', 'quantidade_sessoes')
    search_fields = ('pacote__nome', 'procedimento__nome')
    autocomplete_fields = ('pacote', 'procedimento')
    list_select_related = ('pacote', 'procedimento')

    def has_change_permission(self, request, obj=None):
        if obj is not None and _pacote_vendido(obj.pacote):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and _pacote_vendido(obj.pacote):
            return False
        return super().has_delete_permission(request, obj)


@admin.register(CompraPacote)
class PacoteClienteAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'pacote', 'status', 'valor_pago', 'data_expiracao', 'criado_em')
    list_filter = ('status',)
    search_fields = ('cliente__nome', 'pacote__nome')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    readonly_fields = ('criado_em',)
    autocomplete_fields = ('cliente', 'pacote')
    list_select_related = ('cliente', 'pacote')


@admin.register(ConsumoSessao)
class SessaoPacoteAdmin(admin.ModelAdmin):
    list_display = ('compra_pacote', 'atendimento', 'criado_em')
    search_fields = (
        'compra_pacote__cliente__nome',
        'compra_pacote__pacote__nome',
    )
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    autocomplete_fields = ('compra_pacote', 'atendimento')
    list_select_related = ('compra_pacote', 'atendimento', 'compra_pacote__cliente')


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
    """Sem regra ativa nenhuma comissao e gerada (ComissaoService.calcular_comissao).

    Percentual OU valor fixo (CHECK no banco). Profissional/procedimento vazios
    = vale para todos; a regra mais especifica vence.
    """
    list_display = ('__str__', 'profissional', 'procedimento', 'percentual', 'valor', 'ativo')
    list_filter = ('ativo', 'profissional')
    search_fields = ('profissional__nome', 'procedimento__nome')
    autocomplete_fields = ('profissional', 'procedimento')
    list_select_related = ('profissional', 'procedimento')


@admin.register(MovimentoComissao)
class MovimentoComissaoAdmin(admin.ModelAdmin):
    """Somente leitura: gerado pelo servico e pago pela tela Comissoes do painel."""
    list_display = ('criado_em', 'profissional', 'atendimento', 'valor', 'status', 'pago_em')
    list_filter = ('status', 'profissional')
    search_fields = ('profissional__nome', 'atendimento__cliente__nome')
    ordering = ('-criado_em',)
    date_hierarchy = 'criado_em'
    list_select_related = ('profissional', 'atendimento', 'atendimento__cliente')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Feriado)
class FeriadoAdmin(admin.ModelAdmin):
    """Feriados e recessos da clinica (bloqueiam a agenda se bloqueia_agendamento)."""
    list_display = ('data', 'nome', 'escopo', 'bloqueia_agendamento')
    list_filter = ('escopo', 'bloqueia_agendamento')
    search_fields = ('nome',)
    ordering = ('-data',)
    date_hierarchy = 'data'
