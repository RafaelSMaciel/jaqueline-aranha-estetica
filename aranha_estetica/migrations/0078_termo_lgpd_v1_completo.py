# P09 (decisao 47): a Politica de Privacidade passa a ser o texto da VersaoTermo LGPD vigente, e a v1.0
# e reescrita com a politica completa (conteudo/lgpd.TERMO_LGPD_CONTEUDO) — valido SO porque nao ha
# producao: num banco novo a 0045 ja grava este texto e esta migration nao muda nada; num banco de
# desenvolvimento (ou de testes com --keepdb) troca o resumo antigo pelo texto novo.
#
# Guarda: v1.0 com aceite e prova legal (o SHA-256 do texto aceito esta no aceite_termo) — aborta com
# os ids em vez de reescrever. Depois do go-live, politica nova = versao nova pelo painel.
# Reverso: no-op (o texto antigo nao e restaurado; nenhuma prova depende dele).
from django.db import migrations

from aranha_estetica.conteudo.lgpd import TERMO_LGPD_CONTEUDO, TERMO_LGPD_TITULO, TERMO_LGPD_VERSAO


def reescrever_v1(apps, schema_editor):
    VersaoTermo = apps.get_model('aranha_estetica', 'VersaoTermo')
    AceiteTermo = apps.get_model('aranha_estetica', 'AceiteTermo')
    for termo in VersaoTermo.objects.filter(tipo='LGPD', procedimento__isnull=True, versao=TERMO_LGPD_VERSAO):
        if termo.conteudo == TERMO_LGPD_CONTEUDO:
            continue
        aceites = sorted(AceiteTermo.objects.filter(versao_termo_id=termo.pk).values_list('pk', flat=True))
        if aceites:
            raise RuntimeError(
                f'0078: o termo LGPD v{TERMO_LGPD_VERSAO} (versao_termo.id={termo.pk}) ja foi aceito '
                f'(aceite_termo.id {aceites}): o texto aceito e prova e nao pode ser reescrito. Publique a '
                'politica nova como versao nova (Painel > Termos) ou, em banco de desenvolvimento, recrie-o.'
            )
        VersaoTermo.objects.filter(pk=termo.pk).update(conteudo=TERMO_LGPD_CONTEUDO, titulo=TERMO_LGPD_TITULO)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0077_retencao_saude_pacote'),
    ]

    operations = [
        migrations.RunPython(reescrever_v1, migrations.RunPython.noop),
    ]
