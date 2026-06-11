# Remodelagem v2.1 — Fase 1a (docs/specs/remodelagem-banco-v2.md)
# Corta 8 models especulativos sem nenhum uso (zero refs em views/services/admin)
# + remove 3 indices redundantes. DeleteModel puro em ordem de dependencia
# (filhos antes de pais) — sem RemoveField intermediario, que gera estado
# inconsistente no autodetector com Meta.indexes.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0026_otpcode_canal_default_sms'),
    ]

    operations = [
        # ── filhos primeiro (FKs apontando p/ outros mortos) ──
        migrations.DeleteModel(name='ClienteTag'),          # FK -> Tag, Cliente, Usuario
        migrations.DeleteModel(name='ItemPlanoTratamento'), # FK -> PlanoTratamento, Procedimento
        migrations.DeleteModel(name='MovimentoEstoque'),    # FK -> Produto, Atendimento, Usuario
        # ── pais ──
        migrations.DeleteModel(name='Tag'),
        migrations.DeleteModel(name='PlanoTratamento'),
        migrations.DeleteModel(name='Produto'),
        # ── independentes ──
        migrations.DeleteModel(name='PatchTest'),
        migrations.DeleteModel(name='FotoAntesDepois'),
        # ── indices redundantes ──
        migrations.RemoveIndex(model_name='atendimento', name='idx_atendimento_cliente'),
        migrations.RemoveIndex(model_name='cliente', name='idx_cliente_email'),
        migrations.RemoveIndex(model_name='feriado', name='idx_feriado_data'),
        # email: tira db_index do campo (UNIQUE parcial uniq_cliente_email_ativo ja indexa)
        migrations.AlterField(
            model_name='cliente',
            name='email',
            field=models.EmailField(blank=True, max_length=254, null=True),
        ),
    ]
