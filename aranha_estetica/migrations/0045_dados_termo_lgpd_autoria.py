# Rodada 2 da auditoria pre-producao (dados):
#  - AnotacaoSessao.autor_nome <- Usuario.nome das notas existentes.
#  - Notificacao LEMBRETE/EMAIL -> TERMO: o unico criador de LEMBRETE por e-mail
#    era o link do termo do booking (os lembretes reais sao WHATSAPP); com tipo
#    proprio, /termo/ aceita so token de termo e /confirmar/ nunca aceita.
#  - VersaoTermo LGPD v1.0 (resumo fiel de /politica-de-privacidade/) quando o
#    banco ja opera (tem clientes) e nao ha termo LGPD ativo: sem versao vigente
#    o booking nao tem o que registrar em AceiteTermo. Banco novo (dev, testes,
#    instalacao limpa) recebe o termo pelo `seed` ou por Painel > Termos.
# Aceites antigos ficam com conteudo_sha256 vazio: o texto aceito na epoca nao
# e comprovavel (a versao podia ser editada), e inventar o hash seria pior.

from django.db import migrations
from django.db.models import OuterRef, Subquery


def preencher_autor_nome(apps, schema_editor):
    AnotacaoSessao = apps.get_model('aranha_estetica', 'AnotacaoSessao')
    Usuario = apps.get_model('aranha_estetica', 'Usuario')
    AnotacaoSessao.objects.filter(autor__isnull=False, autor_nome='').update(
        autor_nome=Subquery(Usuario.objects.filter(pk=OuterRef('autor_id')).values('nome')[:1]),
    )


def notificacao_termo(apps, schema_editor):
    Notificacao = apps.get_model('aranha_estetica', 'Notificacao')
    Notificacao.objects.filter(tipo='LEMBRETE', canal='EMAIL').update(tipo='TERMO')


def notificacao_termo_reverso(apps, schema_editor):
    # canal=EMAIL de proposito: no codigo antigo LEMBRETE/EMAIL = termo; um
    # TERMO/WHATSAPP revertido p/ LEMBRETE/WHATSAPP valeria em /confirmar/.
    Notificacao = apps.get_model('aranha_estetica', 'Notificacao')
    Notificacao.objects.filter(tipo='TERMO').update(tipo='LEMBRETE', canal='EMAIL')


def criar_termo_lgpd_v1(apps, schema_editor):
    from django.utils import timezone

    from aranha_estetica.constants import (
        TERMO_LGPD_CONTEUDO, TERMO_LGPD_TITULO, TERMO_LGPD_VERSAO,
    )

    VersaoTermo = apps.get_model('aranha_estetica', 'VersaoTermo')
    Cliente = apps.get_model('aranha_estetica', 'Cliente')
    LogAuditoria = apps.get_model('aranha_estetica', 'LogAuditoria')
    if VersaoTermo.objects.filter(tipo='LGPD', procedimento__isnull=True, ativa=True).exists():
        return
    if not Cliente.objects.exists():
        return  # banco novo: seed / Painel > Termos
    termo = VersaoTermo.objects.create(
        tipo='LGPD', procedimento=None, versao=TERMO_LGPD_VERSAO,
        titulo=TERMO_LGPD_TITULO, conteudo=TERMO_LGPD_CONTEUDO,
        vigente_desde=timezone.localdate(), ativa=True,
    )
    LogAuditoria.objects.create(
        acao=f'migration 0045: termo LGPD v{TERMO_LGPD_VERSAO} publicado (resumo da politica de privacidade)',
        tabela='versao_termo',
        registro_id=termo.pk,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0044_prova_aceite_autoria_anotacao_ajustes'),
    ]

    operations = [
        migrations.RunPython(preencher_autor_nome, migrations.RunPython.noop),
        migrations.RunPython(notificacao_termo, notificacao_termo_reverso),
        # reverso noop: o termo pode ja ter aceites (RESTRICT) — nao se apaga prova
        migrations.RunPython(criar_termo_lgpd_v1, migrations.RunPython.noop),
    ]
