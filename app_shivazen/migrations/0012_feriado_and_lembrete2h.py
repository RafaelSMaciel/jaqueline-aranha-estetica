from django.db import migrations, models


def seed_feriados_nacionais(apps, schema_editor):
    """Insere feriados nacionais fixos 2026-2027 (exceto moveis: Carnaval, Paixao, Corpus Christi)."""
    Feriado = apps.get_model('app_shivazen', 'Feriado')

    fixos = [
        ('01-01', 'Confraternizacao Universal'),
        ('04-21', 'Tiradentes'),
        ('05-01', 'Dia do Trabalho'),
        ('09-07', 'Independencia do Brasil'),
        ('10-12', 'Nossa Senhora Aparecida'),
        ('11-02', 'Finados'),
        ('11-15', 'Proclamacao da Republica'),
        ('11-20', 'Dia da Consciencia Negra'),
        ('12-25', 'Natal'),
    ]
    # Feriados moveis 2026 e 2027 (datas publicas oficiais).
    moveis = [
        ('2026-02-17', 'Carnaval'),
        ('2026-02-18', 'Quarta-feira de Cinzas (meio dia)'),
        ('2026-04-03', 'Sexta-feira Santa'),
        ('2026-06-04', 'Corpus Christi'),
        ('2027-02-09', 'Carnaval'),
        ('2027-03-26', 'Sexta-feira Santa'),
        ('2027-05-27', 'Corpus Christi'),
    ]

    novos = []
    for ano in (2026, 2027):
        for md, nome in fixos:
            novos.append({'data': f'{ano}-{md}', 'nome': nome})
    for d, nome in moveis:
        novos.append({'data': d, 'nome': nome})

    for item in novos:
        Feriado.objects.get_or_create(
            data=item['data'],
            escopo='NACIONAL',
            defaults={'nome': item['nome'], 'bloqueia_agendamento': True},
        )


def unseed_feriados(apps, schema_editor):
    Feriado = apps.get_model('app_shivazen', 'Feriado')
    Feriado.objects.filter(escopo='NACIONAL').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app_shivazen', '0011_otpcode'),
    ]

    operations = [
        migrations.CreateModel(
            name='Feriado',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('data', models.DateField()),
                ('nome', models.CharField(max_length=120)),
                ('escopo', models.CharField(
                    choices=[('NACIONAL', 'Nacional'), ('LOCAL', 'Local (estadual/municipal)'), ('CLINICA', 'Recesso da clinica')],
                    default='NACIONAL', max_length=20,
                )),
                ('bloqueia_agendamento', models.BooleanField(
                    default=True,
                    help_text='Se True, impede geracao de horarios livres nesta data.',
                )),
                ('observacao', models.CharField(blank=True, max_length=255, null=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['data'],
                'managed': True,
                'db_table': 'feriado',
            },
        ),
        migrations.AddConstraint(
            model_name='feriado',
            constraint=models.UniqueConstraint(fields=('data', 'escopo'), name='uq_feriado_data_escopo'),
        ),
        migrations.AddIndex(
            model_name='feriado',
            index=models.Index(fields=['data'], name='idx_feriado_data'),
        ),
        # Ampliar choices de Notificacao.tipo (adiciona LEMBRETE_2H).
        migrations.AlterField(
            model_name='notificacao',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('LEMBRETE', 'Lembrete D-1'),
                    ('LEMBRETE_2H', 'Lembrete T-2h'),
                    ('CONFIRMACAO', 'Confirmação'),
                    ('CANCELAMENTO', 'Cancelamento'),
                    ('NPS', 'Pesquisa NPS'),
                    ('APROVACAO', 'Aprovação Profissional'),
                ],
                default='LEMBRETE', max_length=30,
            ),
        ),
        migrations.RunPython(seed_feriados_nacionais, unseed_feriados),
    ]
