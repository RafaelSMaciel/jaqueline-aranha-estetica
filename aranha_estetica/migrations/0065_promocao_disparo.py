# P11 (comunicacao): o disparo da promocao guarda o proprio estado (services/marketing).
#
# promocao.envio_cursor / disparada_em: o envio em lotes deixa de guardar o cursor numa linha de
# configuracao ('promocao_<pk>_envio_cursor', que a tela de Configuracoes listava e deixava apagar —
# apagar fazia o proximo disparo reenviar a quem ja recebeu). As linhas antigas saem aqui (envio em
# andamento recomeca do inicio so se alguem disparar de novo, agora com a confirmacao de reenvio).
from django.db import migrations, models

CHAVE_CURSOR = r'^promocao_[0-9]+_envio_cursor$'


def apagar_cursores_em_configuracao(apps, schema_editor):
    Configuracao = apps.get_model('aranha_estetica', 'Configuracao')
    Configuracao.objects.filter(chave__regex=CHAVE_CURSOR).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0064_carteira_rotulos'),
    ]

    operations = [
        migrations.AddField(
            model_name='promocao',
            name='envio_cursor',
            field=models.BigIntegerField(
                blank=True, null=True,
                db_comment='Disparo por e-mail em andamento: ultimo cliente.id ja reservado p/ envio '
                           '(NULL = nao iniciado ou concluido).',
            ),
        ),
        migrations.AddField(
            model_name='promocao',
            name='disparada_em',
            field=models.DateTimeField(
                blank=True, null=True,
                db_comment='Inicio do ultimo disparo por e-mail (NULL = nunca disparada). Novo disparo so com '
                           'confirmacao de reenvio.',
            ),
        ),
        migrations.RunPython(apagar_cursores_em_configuracao, migrations.RunPython.noop),
    ]
