# P11: 'PARAR' no WhatsApp revoga lembrete e pesquisa por WhatsApp com a origem WHATSAPP
# (views/infra/webhooks). Recria o ck_consentimento_origem com o valor novo (paridade com o SQLite).
# O P09 tambem cria ORIGEM_WHATSAPP (+ PORTAL): na integracao fica UMA migration com as duas origens.
from django.db import migrations, models

ORIGENS = [
    ('AGENDAMENTO_ONLINE', 'Agendamento online (a própria cliente)'),
    ('DESCADASTRO', 'Link de descadastro'),
    ('PAINEL', 'Painel (equipe)'),
    ('ESQUECIMENTO', 'Direito ao esquecimento'),
    ('MIGRACAO', 'Migração do cadastro antigo'),
    ('ADMIN', 'Django admin'),
    ('WHATSAPP', 'Resposta no WhatsApp'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0065_promocao_disparo'),
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
    ]
