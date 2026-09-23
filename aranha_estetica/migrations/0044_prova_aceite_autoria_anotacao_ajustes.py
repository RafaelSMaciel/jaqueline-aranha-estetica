# Rodada 2 da auditoria pre-producao (schema):
#  - AceiteTermo.conteudo_sha256: prova do texto aceito (vazio = aceite antigo).
#  - AnotacaoSessao: autor PROTECT (excluir o usuario apagava a autoria do
#    registro clinico) + autor_nome (nome no momento da escrita; backfill na 0045).
#  - ListaEspera.email_contato: e-mail do formulario publico p/ cliente existente.
#  - Notificacao.tipo += TERMO (link /termo/<token>/; migracao dos tokens na 0045).
#  - Preco.vigente_desde default localdate (date.today = relogio UTC do container).
#  - Usuario.papel default PROFISSIONAL (RECEPCAO nao tem login).
#  - RegraComissao.percentual 0..100 (validator + CHECK). Pre-limpeza: valor fora
#    da faixa e travado em 0/100 com LogAuditoria (mesmo criterio da 0034 p/ promocao).
# on_delete/default/choices/validators nao geram SQL; so as colunas novas e o CHECK.

from decimal import Decimal

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def travar_percentual_comissao(apps, schema_editor):
    RegraComissao = apps.get_model('aranha_estetica', 'RegraComissao')
    LogAuditoria = apps.get_model('aranha_estetica', 'LogAuditoria')
    fora = (
        RegraComissao.objects
        .filter(models.Q(percentual__gt=100) | models.Q(percentual__lt=0))
        .values_list('pk', 'percentual')
    )
    for pk, percentual in list(fora):
        novo = Decimal('100.00') if percentual > 100 else Decimal('0.00')
        RegraComissao.objects.filter(pk=pk).update(percentual=novo)
        LogAuditoria.objects.create(
            acao='migration 0044: percentual de comissao fora de 0..100 ajustado',
            tabela='regra_comissao',
            registro_id=pk,
            detalhes={'de': str(percentual), 'para': str(novo)},
        )
    if schema_editor.connection.vendor == 'postgresql':
        # UPDATEs acima nao podem deixar trigger events pendentes p/ o ALTER TABLE
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0043_retorno_unico_checks_jsonb'),
    ]

    operations = [
        migrations.AddField(
            model_name='aceitetermo',
            name='conteudo_sha256',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='anotacaosessao',
            name='autor_nome',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='listaespera',
            name='email_contato',
            field=models.EmailField(blank=True, max_length=254, null=True),
        ),
        migrations.AlterField(
            model_name='anotacaosessao',
            name='autor',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='notificacao',
            name='tipo',
            field=models.CharField(choices=[('LEMBRETE', 'Lembrete D-1'), ('LEMBRETE_2H', 'Lembrete T-2h'), ('CONFIRMACAO', 'Confirmação'), ('CANCELAMENTO', 'Cancelamento'), ('NPS', 'Pesquisa NPS'), ('PESQUISA', 'Pesquisa de satisfação detalhada'), ('APROVACAO', 'Aprovação Profissional'), ('TERMO', 'Termo de consentimento')], default='LEMBRETE', max_length=30),
        ),
        migrations.AlterField(
            model_name='preco',
            name='vigente_desde',
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
        migrations.AlterField(
            model_name='regracomissao',
            name='percentual',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Ex: 30.00 para 30% (0 a 100). Se preenchido, valor deve ser nulo.', max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0.00')), django.core.validators.MaxValueValidator(Decimal('100.00'))]),
        ),
        migrations.AlterField(
            model_name='usuario',
            name='papel',
            field=models.CharField(choices=[('ADMIN', 'Administrador'), ('PROFISSIONAL', 'Profissional'), ('RECEPCAO', 'Recepcao')], default='PROFISSIONAL', max_length=20),
        ),
        migrations.RunPython(travar_percentual_comissao, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='regracomissao',
            constraint=models.CheckConstraint(condition=models.Q(('percentual__isnull', True), models.Q(('percentual__gte', 0), ('percentual__lte', 100)), _connector='OR'), name='chk_regra_comissao_percentual_0_100'),
        ),
    ]
