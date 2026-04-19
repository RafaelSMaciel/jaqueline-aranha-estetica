from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app_shivazen', '0012_feriado_and_lembrete2h'),
    ]

    operations = [
        # ListaEspera: lookup por cliente (painel cliente_detalhe) + por procedimento/data (fila).
        migrations.AddIndex(
            model_name='listaespera',
            index=models.Index(fields=['cliente'], name='idx_espera_cliente'),
        ),
        migrations.AddIndex(
            model_name='listaespera',
            index=models.Index(
                fields=['procedimento', 'data_desejada', 'notificado'],
                name='idx_espera_proc_data_notif',
            ),
        ),
        # AnotacaoSessao: lookups por atendimento (prontuario_detalhe).
        migrations.AddIndex(
            model_name='anotacaosessao',
            index=models.Index(fields=['atendimento', '-criado_em'], name='idx_anot_atn_criado'),
        ),
        # SessaoPacote: lookup por pacote_cliente (calculo de sessoes restantes).
        migrations.AddIndex(
            model_name='sessaopacote',
            index=models.Index(fields=['pacote_cliente'], name='idx_sessao_pct_cli'),
        ),
    ]
