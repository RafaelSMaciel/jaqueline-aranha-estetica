# app_shivazen/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import *

# Registros simples (para visualização padrão)
admin.site.register(Funcionalidade)
admin.site.register(Perfil)
admin.site.register(PerfilFuncionalidade)
admin.site.register(Profissional)
admin.site.register(Cliente)
admin.site.register(Prontuario)
admin.site.register(ProntuarioPergunta)
admin.site.register(Procedimento)
admin.site.register(ProfissionalProcedimento)
admin.site.register(Preco)
admin.site.register(DisponibilidadeProfissional)
admin.site.register(BloqueioAgenda)
admin.site.register(Atendimento)
admin.site.register(ProntuarioResposta)
admin.site.register(Notificacao)
admin.site.register(TermoConsentimento)
admin.site.register(LogAuditoria)

# Novos Modelos Premium
admin.site.register(Pacote)
admin.site.register(ItemPacote)
admin.site.register(PacoteCliente)
admin.site.register(SessaoPacote)
admin.site.register(ListaEspera)
admin.site.register(AvaliacaoNPS)
admin.site.register(MetaProfissional)
admin.site.register(TokenGoogleAgenda)
# --- CONFIGURAÇÃO CORRIGIDA PARA O ADMIN DE USUÁRIO ---
# Substitua a classe UsuarioAdmin antiga por esta:

@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    """
    Define a visualização admin para o modelo de Usuário customizado (Raw SQL schema).
    """
    list_display = ('email', 'nome', 'perfil', 'ativo')
    list_filter = ('perfil', 'ativo')
    search_fields = ('nome', 'email')
    ordering = ('email',)