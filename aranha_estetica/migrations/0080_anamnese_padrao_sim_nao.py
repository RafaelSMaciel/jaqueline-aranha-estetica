# Ficha "Anamnese padrao" (questionario pre-atendimento do agendamento online): passa a ser
# obrigatoria, com perguntas sim/nao e o texto so como detalhe de um Sim ("se" — services/anamnese).
# Antes era opcional e os campos de texto aceitavam qualquer coisa (conteudo/anamnese.py explica).
#
# So troca a ficha que ainda esta IGUAL a que o seed gravava: ficha que a clinica ja editou pelo
# painel fica como esta. Banco novo: nao ha ficha (o seed ja cria a nova).
#
# A troca e NO LUGAR (mesmo id), mesmo com respostas gravadas — de proposito, ao contrario da edicao
# pelo painel (que cria uma versao nova e desativa a antiga): as perguntas sao as mesmas, so mudou o
# formato (as keys antigas `alergias` e `procedimentos_recentes` continuam, agora como detalhe de um
# Sim). Uma versao nova (id novo) tiraria a validade de 12 meses de quem ja respondeu (D14): toda
# cliente seria perguntada de novo e todo atendimento da agenda ganharia o aviso "ficha pendente".
# Reverso: no-op (a ficha antiga nao e restaurada; nenhuma resposta depende disso).
from django.db import migrations

from aranha_estetica.conteudo.anamnese import ANAMNESE_PADRAO, ANAMNESE_PADRAO_ANTIGA, ANAMNESE_PADRAO_NOME


def atualizar_ficha_padrao(apps, schema_editor):
    Formulario = apps.get_model('aranha_estetica', 'FormularioAnamnese')
    fichas = Formulario.objects.filter(nome=ANAMNESE_PADRAO_NOME, tipo='ANAMNESE', ativo=True)
    for ficha in fichas:
        if ficha.schema_json == ANAMNESE_PADRAO_ANTIGA:
            Formulario.objects.filter(pk=ficha.pk).update(schema_json=ANAMNESE_PADRAO, obrigatorio=True)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0079_metadados_em_ingles'),
    ]

    operations = [
        migrations.RunPython(atualizar_ficha_padrao, migrations.RunPython.noop),
    ]
