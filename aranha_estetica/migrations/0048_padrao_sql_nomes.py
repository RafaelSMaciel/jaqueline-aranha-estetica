# Padrao SQL (docs/specs/padrao-sql.md) — parte 1: nomes.
#
# - Indices: idx_<tabela ou sigla>_<colunas|proposito> (<= 30 chars, limite do
#   Django). RenameIndex = ALTER INDEX ... RENAME no Postgres (so catalogo).
# - Constraints: <prefixo>_<tabela>_<descricao>. RenomearConstraint troca so o
#   nome (sem revalidar CHECK nem reconstruir indice no Postgres).
# - Colunas: profissional.min_notice_horas/max_advance_dias ->
#   antecedencia_minima_horas/antecedencia_maxima_dias; assinatura_push.user_id
#   -> usuario_id (PT-BR, como o resto do schema).
# As tabelas da agenda (disponibilidade_profissional, bloqueio_agenda,
# excecao_disponibilidade) ficam como estao: a Fase 7 da remodelagem as troca.
# Nada aqui mexe em dado nem pode abortar.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

from ._operacoes import RenomearConstraint

INDICES = [
    # (model, antigo, novo)
    ('atendimento', 'idx_atend_prof_data', 'idx_atendimento_prof_data'),
    ('notificacao', 'idx_notif_nps_lookup', 'idx_notificacao_nps_lookup'),
    ('notificacao', 'idx_notif_atend_tipo', 'idx_notificacao_atend_tipo'),
    ('prontuarioversao', 'idx_pront_versao', 'idx_prontuario_versao_criado'),
    ('anotacaosessao', 'idx_anot_atn_criado', 'idx_anotacao_sessao_atend_data'),
    ('aceitetermo', 'idx_aceite_cli_data', 'idx_aceite_termo_cli_data'),
    ('comprapacote', 'idx_pacote_cli_status', 'idx_compra_pacote_cli_status'),
    ('listaespera', 'idx_espera_proc_data_notif', 'idx_lista_espera_busca'),
    ('logauditoria', 'idx_auditoria_tabela', 'idx_log_auditoria_tabela'),
    ('logauditoria', 'idx_auditoria_criado', 'idx_log_auditoria_criado'),
    ('logauditoria', 'idx_auditoria_user_data', 'idx_log_auditoria_usuario_data'),
    ('codigootp', 'idx_otp_email_data', 'idx_codigo_otp_email_data'),
    ('codigootp', 'idx_otp_prop_data', 'idx_codigo_otp_prop_data'),
    ('formularioanamnese', 'idx_anamnese_ativo_escopo', 'idx_form_anamnese_ativo_escopo'),
    ('formularioanamnese', 'idx_anamnese_ativo_tipo', 'idx_form_anamnese_ativo_tipo'),
    ('respostaanamnese', 'idx_anamnese_resp_cli', 'idx_resp_anamnese_cli_data'),
    ('respostaanamnese', 'idx_anamnese_resp_data', 'idx_resp_anamnese_respondida'),
    ('movimentocarteira', 'idx_movcart_cart', 'idx_mov_carteira_cart_data'),
    ('movimentocarteira', 'idx_movcred_origem_tipo', 'idx_mov_carteira_origem_tipo'),
    ('movimentocomissao', 'idx_comissao_prof_status', 'idx_mov_comissao_prof_status'),
    ('movimentocomissao', 'idx_comissao_criado', 'idx_mov_comissao_criado'),
]

CONSTRAINTS = [
    ('atendimento', 'chk_atendimento_status_v2', 'chk_atendimento_status'),
    ('atendimento', 'chk_retorno_tem_origem', 'chk_atendimento_retorno_tem_origem'),
    ('atendimento', 'chk_retorno_valor_zero', 'chk_atendimento_retorno_valor_zero'),
    ('atendimento', 'uniq_retorno_por_origem', 'uniq_atendimento_retorno_por_origem'),
    ('versaotermo', 'uniq_termo_ativo_por_escopo', 'uniq_versao_termo_ativo_por_escopo'),
    ('versaotermo', 'uniq_termo_ativo_global', 'uniq_versao_termo_ativo_global'),
    ('aceitetermo', 'uniq_aceite_cliente_versao', 'uniq_aceite_termo_cliente_versao'),
    ('comprapacote', 'chk_pacote_cliente_status', 'chk_compra_pacote_status'),
    ('listaespera', 'uniq_espera_ativa', 'uniq_lista_espera_ativa'),
    ('feriado', 'uq_feriado_data_escopo', 'uniq_feriado_data_escopo'),
    ('codigootp', 'chk_otp_tentativas_teto', 'chk_codigo_otp_tentativas_teto'),
    ('movimentocarteira', 'uniq_cashback_indicacao_por_atendimento',
     'uniq_movimento_carteira_indicacao_por_atendimento'),
    ('regracomissao', 'chk_regra_comissao_percentual_xor_fixo', 'chk_regra_comissao_percentual_xor_valor'),
    ('movimentocomissao', 'uniq_comissao_ativa_por_atendimento',
     'uniq_movimento_comissao_ativa_por_atendimento'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0047_termo_saude_reembolso_promocao_geral'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        *[
            migrations.RenameIndex(model_name=m, old_name=antigo, new_name=novo)
            for m, antigo, novo in INDICES
        ],
        *[
            RenomearConstraint(model_name=m, old_name=antigo, new_name=novo)
            for m, antigo, novo in CONSTRAINTS
        ],
        # profissional: a CHECK cita a coluna, entao sai e volta com o nome novo
        migrations.RemoveConstraint(model_name='profissional', name='chk_profissional_min_notice_range'),
        migrations.RemoveConstraint(model_name='profissional', name='chk_profissional_max_advance_range'),
        migrations.RenameField(
            model_name='profissional', old_name='min_notice_horas', new_name='antecedencia_minima_horas',
        ),
        migrations.RenameField(
            model_name='profissional', old_name='max_advance_dias', new_name='antecedencia_maxima_dias',
        ),
        migrations.AddConstraint(
            model_name='profissional',
            constraint=models.CheckConstraint(
                condition=models.Q(('antecedencia_minima_horas__gte', 0), ('antecedencia_minima_horas__lte', 720)),
                name='chk_profissional_antecedencia_minima',
            ),
        ),
        migrations.AddConstraint(
            model_name='profissional',
            constraint=models.CheckConstraint(
                condition=models.Q(('antecedencia_maxima_dias__gte', 1), ('antecedencia_maxima_dias__lte', 365)),
                name='chk_profissional_antecedencia_maxima',
            ),
        ),
        # assinatura_push.user_id -> usuario_id. O indice composto cita a coluna:
        # sai antes do rename e volta depois (ele tambem cobre a FK sozinha).
        migrations.RemoveIndex(model_name='assinaturapush', name='idx_pushsub_user_ativo'),
        migrations.RenameField(model_name='assinaturapush', old_name='user', new_name='usuario'),
        migrations.AlterField(
            model_name='assinaturapush',
            name='usuario',
            field=models.ForeignKey(
                db_index=False, on_delete=django.db.models.deletion.CASCADE,
                related_name='push_subscriptions', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddIndex(
            model_name='assinaturapush',
            index=models.Index(fields=['usuario', 'ativo'], name='idx_assinatura_push_usu_ativo'),
        ),
    ]
