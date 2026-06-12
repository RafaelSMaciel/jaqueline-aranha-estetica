# Remodelagem v2.1 — Fase 2b (docs/specs/remodelagem-banco-v2.md secao 4)
# Renames de modelo + tabela fisica + 2 campos FK internos.
# Escrita a mao: autodetector nao resolve rename simultaneo de classe+campo.
# RenameModel/AlterModelTable/RenameField = ALTER ... RENAME (zero copia).

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0031_remodelagem_fase2a_renames_colunas'),
    ]

    operations = [
        # ── classes (estado Django) ──
        migrations.RenameModel(old_name='OtpCode', new_name='CodigoOtp'),
        migrations.RenameModel(old_name='CreditoCliente', new_name='Carteira'),
        migrations.RenameModel(old_name='MovimentoCredito', new_name='MovimentoCarteira'),
        migrations.RenameModel(old_name='PacoteCliente', new_name='CompraPacote'),
        migrations.RenameModel(old_name='SessaoPacote', new_name='ConsumoSessao'),
        migrations.RenameModel(old_name='WebPushSubscription', new_name='AssinaturaPush'),
        migrations.RenameModel(old_name='ConfiguracaoSistema', new_name='Configuracao'),
        migrations.RenameModel(old_name='ProfissionalProcedimento', new_name='Habilitacao'),
        # ── tabelas fisicas ──
        migrations.AlterModelTable(name='codigootp', table='codigo_otp'),
        migrations.AlterModelTable(name='carteira', table='carteira'),
        migrations.AlterModelTable(name='movimentocarteira', table='movimento_carteira'),
        migrations.AlterModelTable(name='comprapacote', table='compra_pacote'),
        migrations.AlterModelTable(name='consumosessao', table='consumo_sessao'),
        migrations.AlterModelTable(name='assinaturapush', table='assinatura_push'),
        migrations.AlterModelTable(name='configuracao', table='configuracao'),
        migrations.AlterModelTable(name='habilitacao', table='habilitacao'),
        # ── constraints/indexes dependentes saem antes dos renames de campo ──
        migrations.RemoveIndex(model_name='movimentocarteira', name='idx_movcred_cred'),
        migrations.RemoveConstraint(model_name='movimentocarteira', name='uniq_cashback_indicacao_por_atendimento'),
        migrations.RemoveIndex(model_name='consumosessao', name='idx_sessao_pct_cli'),
        # ── campos FK internos ──
        migrations.RenameField(model_name='movimentocarteira', old_name='credito', new_name='carteira'),
        migrations.RenameField(model_name='consumosessao', old_name='pacote_cliente', new_name='compra_pacote'),
        # related_name Carteira.cliente: credito -> carteira (no-op no banco)
        migrations.AlterField(
            model_name='carteira',
            name='cliente',
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='carteira',
                to='aranha_estetica.cliente',
            ),
        ),
        # ── recria constraints/indexes com nomes novos ──
        migrations.AddIndex(
            model_name='movimentocarteira',
            index=models.Index(fields=['carteira', '-criado_em'], name='idx_movcart_cart'),
        ),
        migrations.AddConstraint(
            model_name='movimentocarteira',
            constraint=models.UniqueConstraint(
                condition=models.Q(('origem', 'CASHBACK_INDICACAO')),
                fields=('carteira', 'atendimento'),
                name='uniq_cashback_indicacao_por_atendimento',
            ),
        ),
        migrations.AddIndex(
            model_name='consumosessao',
            index=models.Index(fields=['compra_pacote'], name='idx_sessao_pct_cli'),
        ),
    ]
