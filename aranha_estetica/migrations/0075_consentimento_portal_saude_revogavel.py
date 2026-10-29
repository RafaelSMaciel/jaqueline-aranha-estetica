# P09 (decisoes 32 e 39): a area da cliente ("Meus agendamentos") registra preferencias de comunicacao
# com a origem PORTAL, e o consentimento de dado de saude (art. 11 I) passa a ser revogavel —
# cliente.saude_revogada_em (NULL = vigente ou nunca dado; um aceite SAUDE posterior reativa).
# Recria o ck_consentimento_origem com o valor novo (paridade com o SQLite). Sem dado a migrar.
from django.db import migrations, models

ORIGENS = [
    ('AGENDAMENTO_ONLINE', 'Agendamento online (a própria cliente)'),
    ('DESCADASTRO', 'Link de descadastro'),
    ('PAINEL', 'Painel (equipe)'),
    ('ESQUECIMENTO', 'Direito ao esquecimento'),
    ('MIGRACAO', 'Migração do cadastro antigo'),
    ('ADMIN', 'Django admin'),
    ('WHATSAPP', 'Resposta no WhatsApp'),
    ('PORTAL', 'Área da cliente'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0074_cliente_dados_sem_finalidade'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='consentimento',
            name='ck_consentimento_origem',
        ),
        migrations.AlterField(
            model_name='consentimento',
            name='origem',
            field=models.CharField(choices=ORIGENS, max_length=30),
        ),
        migrations.AddConstraint(
            model_name='consentimento',
            constraint=models.CheckConstraint(
                condition=models.Q(origem__in=[valor for valor, _rotulo in ORIGENS]),
                name='ck_consentimento_origem',
            ),
        ),
        migrations.AddField(
            model_name='cliente',
            name='saude_revogada_em',
            field=models.DateTimeField(
                blank=True, null=True,
                db_comment='Revogacao do consentimento de dado de saude (art. 11 I); NULL = vigente ou nunca dado.',
            ),
        ),
    ]
