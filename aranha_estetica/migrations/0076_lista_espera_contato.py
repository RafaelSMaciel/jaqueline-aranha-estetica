# P10 (decisao 17): a lista de espera publica deixa de criar Cliente. A inscricao anonima guarda so
# nome_contato e telefone_contato; cliente (e email_contato) so com o celular verificado por OTP.
# O aviso nao tira mais da fila: eh_notificado (bool) vira ultimo_aviso_em (quando avisou), e o
# UNIQUE "pendente" vira um por pessoa + procedimento + dia (cliente ou telefone de contato).
#
# Ordem: AddField ultimo_aviso_em -> dados (avisado = atualizado_em) -> guarda de duplicatas (aborta
# com os ids: o UNIQUE novo nao tem mais a condicao "nao notificado") -> sai o UNIQUE antigo e a
# coluna eh_notificado -> colunas de contato, cliente nulavel e as CHECKs/UNIQUEs novas.
# sp_anonimizar_cliente passa a apagar tambem a inscricao anonima pelo telefone na 0077.
# Reverso (dev/testes): volta eh_notificado = (ultimo_aviso_em IS NOT NULL); inscricao anonima
# impede o reverso do NOT NULL do cliente (apague-as antes).
import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Count

from aranha_estetica.db.operacoes import PadronizarFks


def avisado_vira_data(apps, schema_editor):
    ListaEspera = apps.get_model('aranha_estetica', 'ListaEspera')
    for espera in ListaEspera.objects.filter(notificado=True).only('pk', 'atualizado_em'):
        ListaEspera.objects.filter(pk=espera.pk).update(ultimo_aviso_em=espera.atualizado_em)


def data_vira_avisado(apps, schema_editor):
    ListaEspera = apps.get_model('aranha_estetica', 'ListaEspera')
    ListaEspera.objects.filter(ultimo_aviso_em__isnull=False).update(notificado=True)


def guarda_duplicatas(apps, schema_editor):
    """A mesma cliente avisada e inscrita de novo no mesmo procedimento/dia violaria o UNIQUE novo."""
    ListaEspera = apps.get_model('aranha_estetica', 'ListaEspera')
    repetidas = (
        ListaEspera.objects.values('cliente_id', 'procedimento_id', 'data_desejada')
        .annotate(qtd=Count('pk')).filter(qtd__gt=1)
    )
    ids = sorted(
        pk for grupo in repetidas
        for pk in ListaEspera.objects.filter(
            cliente_id=grupo['cliente_id'], procedimento_id=grupo['procedimento_id'],
            data_desejada=grupo['data_desejada'],
        ).values_list('pk', flat=True)
    )
    if ids:
        raise RuntimeError(
            f'0076: inscricoes repetidas na lista de espera (lista_espera.id {ids}). Apague as '
            'duplicadas (fica a mais recente) e rode o migrate de novo.'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0075_consentimento_portal_saude_revogavel'),
    ]

    operations = [
        migrations.AddField(
            model_name='listaespera',
            name='ultimo_aviso_em',
            field=models.DateTimeField(
                blank=True, db_comment='Ultimo aviso de vaga entregue; a inscricao continua na fila.', null=True,
            ),
        ),
        migrations.RunPython(avisado_vira_data, data_vira_avisado),
        migrations.RunPython(guarda_duplicatas, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='listaespera',
            name='uq_lista_espera_pendente',
        ),
        migrations.RemoveField(
            model_name='listaespera',
            name='notificado',
        ),
        migrations.AlterModelTableComment(
            name='listaespera',
            table_comment=(
                'Pedido de aviso de vaga. Anonima (nome/telefone_contato) ou ligada a cliente verificada por OTP. '
                'Apagado quando data_desejada passa (sp_aplicar_retencao_lgpd) ou quando a cliente agenda.'
            ),
        ),
        migrations.AddField(
            model_name='listaespera',
            name='nome_contato',
            field=models.CharField(
                blank=True, db_comment='Nome informado na inscricao anonima (sem cadastro).', db_default='',
                default='', max_length=150,
            ),
        ),
        migrations.AddField(
            model_name='listaespera',
            name='telefone_contato',
            field=models.CharField(
                blank=True, max_length=11, null=True,
                db_comment='Celular (so digitos) da inscricao anonima; o aviso so vai por aqui com consentimento.',
            ),
        ),
        migrations.AlterField(
            model_name='listaespera',
            name='cliente',
            field=models.ForeignKey(
                blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE,
                to='aranha_estetica.cliente',
                db_comment='So com o celular verificado por OTP; NULL = inscricao anonima (nome/telefone_contato).',
            ),
        ),
        # o AlterField de FK recria a constraint com nome de hash: volta ao padrao (fk_..., ON DELETE, DEFERRABLE)
        PadronizarFks('lista_espera', ['cliente_id']),
        migrations.AlterField(
            model_name='listaespera',
            name='email_contato',
            field=models.EmailField(
                blank=True, max_length=254, null=True,
                db_comment='E-mail digitado no formulario publico com o celular verificado (nao reescreve o cadastro).',
            ),
        ),
        migrations.AddConstraint(
            model_name='listaespera',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(cliente__isnull=False)
                    | (models.Q(telefone_contato__isnull=False) & ~models.Q(nome_contato=''))
                ),
                name='ck_lista_espera_cliente_ou_contato',
            ),
        ),
        migrations.AddConstraint(
            model_name='listaespera',
            constraint=models.CheckConstraint(
                condition=models.Q(email_contato__isnull=True) | models.Q(cliente__isnull=False),
                name='ck_lista_espera_email_contato_so_verificado',
            ),
        ),
        migrations.AddConstraint(
            model_name='listaespera',
            constraint=models.CheckConstraint(
                condition=models.Q(telefone_contato__isnull=True) | models.Q(telefone_contato__regex=r'^[0-9]{10,11}$'),
                name='ck_lista_espera_telefone_contato_formato',
            ),
        ),
        migrations.AddConstraint(
            model_name='listaespera',
            constraint=models.UniqueConstraint(
                condition=models.Q(cliente__isnull=False), fields=('cliente', 'procedimento', 'data_desejada'),
                name='uq_lista_espera_cliente',
            ),
        ),
        migrations.AddConstraint(
            model_name='listaespera',
            constraint=models.UniqueConstraint(
                condition=models.Q(telefone_contato__isnull=False),
                fields=('telefone_contato', 'procedimento', 'data_desejada'),
                name='uq_lista_espera_telefone_contato',
            ),
        ),
    ]
