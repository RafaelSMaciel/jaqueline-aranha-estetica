# app_shivazen/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
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

admin.site.register(Funcionalidade)
admin.site.register(Perfil)
admin.site.register(PerfilFuncionalidade)
admin.site.register(Profissional)
admin.site.register(DisponibilidadeProfissional)
admin.site.register(BloqueioAgenda)
admin.site.register(ProfissionalProcedimento)
admin.site.register(Procedimento)
admin.site.register(Preco)
admin.site.register(Promocao)
admin.site.register(Cliente)
admin.site.register(Prontuario)
admin.site.register(ProntuarioPergunta)
admin.site.register(ProntuarioResposta)
admin.site.register(AnotacaoSessao)
admin.site.register(VersaoTermo)
admin.site.register(AceitePrivacidade)
admin.site.register(AssinaturaTermoProcedimento)
admin.site.register(Atendimento)
admin.site.register(Notificacao)
admin.site.register(AvaliacaoNPS)
admin.site.register(Pacote)
admin.site.register(ItemPacote)
admin.site.register(PacoteCliente)
admin.site.register(SessaoPacote)
admin.site.register(ListaEspera)
admin.site.register(LogAuditoria)
admin.site.register(ConfiguracaoSistema)
admin.site.register(CodigoVerificacao)


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('email', 'nome', 'perfil', 'ativo')
    list_filter = ('perfil', 'ativo')
    search_fields = ('nome', 'email')
    ordering = ('email',)
