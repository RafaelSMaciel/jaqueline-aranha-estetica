# Padrao SQL (docs/specs/padrao-sql.md) — parte 2: integridade e catalogo.
#
# - COMMENT ON TABLE nas 34 tabelas via db_table_comment (antes: 13, fora do
#   estado do Django pela 0038).
# - Indices redundantes fora (custo de escrita sem ganho de leitura): os 4
#   explicitos que repetiam outro indice e o indice simples das FKs que ja sao
#   prefixo de um indice composto ou UNIQUE (db_index=False).
# - unique_together -> UniqueConstraint nomeada (habilitacao, item_pacote).
# - item_pacote.procedimento e consumo_sessao.compra_pacote: CASCADE -> PROTECT
#   (so no Django; as FKs do Postgres nao tem ON DELETE).
# - log_auditoria.registro_id: integer -> bigint (as PKs sao bigint).
# - CHECK de dominio nos choices que ainda nao tinham (notificacao, feriado,
#   codigo_otp, formulario_anamnese, movimentos). Tabelas com dado de producao:
#   NOT VALID + VALIDATE em savepoint; dado fora do dominio so gera aviso.
# - Postgres: funcoes de trigger com prefixo fn_ e o CHECK jsonb do formulario
#   com o nome da tabela (rename de catalogo; os triggers apontam por OID).
# Nada aqui apaga dado nem pode abortar o upgrade.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from ._operacoes import AdicionarCheckNaoValidado

# (antigo, novo) — objetos so-Postgres, fora do estado do Django
FUNCOES_PG = [
    ('bloquear_mutacao_ledger', 'fn_movimento_carteira_imutavel'),
    ('aceite_termo_imutavel', 'fn_aceite_termo_imutavel'),
    ('versao_termo_imutavel', 'fn_versao_termo_imutavel'),
    ('prontuario_versao_imutavel', 'fn_prontuario_versao_imutavel'),
]
CONSTRAINTS_PG = [
    ('formulario_anamnese', 'chk_formulario_schema_lista', 'chk_formulario_anamnese_schema_lista'),
]


def _renomear_pg(schema_editor, reverso):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cur:
        for antigo, novo in FUNCOES_PG:
            de, para = (novo, antigo) if reverso else (antigo, novo)
            cur.execute('SELECT to_regprocedure(%s) IS NOT NULL', [f'{de}()'])
            if cur.fetchone()[0]:
                schema_editor.execute(f'ALTER FUNCTION {de}() RENAME TO {para}', None)
        for tabela, antigo, novo in CONSTRAINTS_PG:
            de, para = (novo, antigo) if reverso else (antigo, novo)
            cur.execute('SELECT 1 FROM pg_constraint WHERE conname = %s', [de])
            if cur.fetchone():
                schema_editor.execute(f'ALTER TABLE {tabela} RENAME CONSTRAINT {de} TO {para}', None)


def renomear_objetos_pg(apps, schema_editor):
    _renomear_pg(schema_editor, reverso=False)


def desfazer_objetos_pg(apps, schema_editor):
    _renomear_pg(schema_editor, reverso=True)


class Migration(migrations.Migration):

    dependencies = [
        ("aranha_estetica", "0048_padrao_sql_nomes"),
    ]

    operations = [
        migrations.AlterModelTableComment(
            name="aceitetermo",
            table_comment="Prova de aceite de termo (hash do conteudo, IP, user agent). Imutavel por trigger; retencao ~20 anos.",
        ),
        migrations.AlterModelTableComment(
            name="anotacaosessao",
            table_comment="Anotacao clinica de um atendimento, com autor e snapshot do nome.",
        ),
        migrations.AlterModelTableComment(
            name="assinaturapush",
            table_comment="Assinatura Web Push (VAPID) de um usuario da equipe.",
        ),
        migrations.AlterModelTableComment(
            name="atendimento",
            table_comment="Agendamento/sessao. FSM PENDENTE->AGENDADO->CONFIRMADO->REALIZADO|CANCELADO|FALTOU|REAGENDADO. Sobreposicao bloqueada por excl_atendimento_sobreposicao.",
        ),
        migrations.AlterModelTableComment(
            name="avaliacaonps",
            table_comment="Nota NPS (0-10) e depoimento de um atendimento; publicacao exige autorizacao e moderacao.",
        ),
        migrations.AlterModelTableComment(
            name="bloqueioagenda",
            table_comment="Bloqueio de agenda (unico ou recorrente por RRULE); profissional nulo = clinica inteira.",
        ),
        migrations.AlterModelTableComment(
            name="carteira",
            table_comment="Saldo de credito do cliente (cashback, vale, reembolso). CHECK saldo >= 0; mutacao com select_for_update.",
        ),
        migrations.AlterModelTableComment(
            name="cliente",
            table_comment="Cliente da clinica. Soft delete (deletado_em); telefone so digitos e chave natural do booking (uniq_cliente_telefone_ativo).",
        ),
        migrations.AlterModelTableComment(
            name="codigootp",
            table_comment="Desafio OTP (HMAC-SHA256), tentativas com teto e proposito por fluxo. Purga apos 24h.",
        ),
        migrations.AlterModelTableComment(
            name="comprapacote",
            table_comment="Compra de pacote pelo cliente (valor pago, reembolso, expiracao).",
        ),
        migrations.AlterModelTableComment(
            name="configuracao",
            table_comment="Parametros chave/valor editaveis no painel.",
        ),
        migrations.AlterModelTableComment(
            name="consumosessao",
            table_comment="Consumo de uma sessao do pacote por um atendimento (1 por atendimento).",
        ),
        migrations.AlterModelTableComment(
            name="disponibilidadeprofissional",
            table_comment="Grade semanal de atendimento do profissional (dia_semana 1=Dom..7=Sab).",
        ),
        migrations.AlterModelTableComment(
            name="excecaodisponibilidade",
            table_comment="Excecao da grade semanal numa data: FOLGA ou HORARIO_DIFERENTE.",
        ),
        migrations.AlterModelTableComment(
            name="feriado",
            table_comment="Feriado ou recesso que bloqueia a geracao de horarios.",
        ),
        migrations.AlterModelTableComment(
            name="formularioanamnese",
            table_comment="Modelo de questionario (anamnese pre ou pesquisa pos-atendimento); schema_json = lista de campos.",
        ),
        migrations.AlterModelTableComment(
            name="habilitacao",
            table_comment="Procedimentos que cada profissional esta habilitado a realizar.",
        ),
        migrations.AlterModelTableComment(
            name="itempacote",
            table_comment="Procedimento e quantidade de sessoes que compoem um pacote.",
        ),
        migrations.AlterModelTableComment(
            name="listaespera",
            table_comment="Fila de espera por procedimento e data; token de reserva quando uma vaga abre.",
        ),
        migrations.AlterModelTableComment(
            name="logauditoria",
            table_comment="Trilha de auditoria LGPD art. 37: autor (usuario + snapshot usuario_nome), tabela, registro e IP. Retencao 5 anos.",
        ),
        migrations.AlterModelTableComment(
            name="movimentocarteira",
            table_comment="Ledger append-only da carteira, imutavel por trigger (estorno = novo movimento).",
        ),
        migrations.AlterModelTableComment(
            name="movimentocomissao",
            table_comment="Comissao por atendimento realizado (PENDENTE->PAGA|ESTORNADA); 1 ativa por atendimento.",
        ),
        migrations.AlterModelTableComment(
            name="notificacao",
            table_comment="Mensagem ao cliente (lembrete, confirmacao, NPS, termo) por canal, com token de resposta. Texto purgado apos 12 meses.",
        ),
        migrations.AlterModelTableComment(
            name="pacote",
            table_comment="Pacote de sessoes a venda (preco total e validade).",
        ),
        migrations.AlterModelTableComment(
            name="preco",
            table_comment="Preco por procedimento com vigencia; profissional nulo = preco-base.",
        ),
        migrations.AlterModelTableComment(
            name="procedimento",
            table_comment="Catalogo de servicos. exige_retorno dispara o retorno gratuito automatico (F-RET).",
        ),
        migrations.AlterModelTableComment(
            name="profissional",
            table_comment="Profissional de estetica. 1:1 opcional com usuario; antecedencia minima/maxima do agendamento online.",
        ),
        migrations.AlterModelTableComment(
            name="promocao",
            table_comment="Promocao por percentual ou preco fixo, com periodo de vigencia; procedimento nulo = geral.",
        ),
        migrations.AlterModelTableComment(
            name="prontuario",
            table_comment="Ficha de saude permanente do cliente. respostas_extras (JSONB) = questionario configuravel.",
        ),
        migrations.AlterModelTableComment(
            name="prontuarioversao",
            table_comment="Historico append-only da ficha de saude (foto anterior a cada edicao). Imutavel por trigger.",
        ),
        migrations.AlterModelTableComment(
            name="regracomissao",
            table_comment="Regra de comissao por profissional/procedimento: percentual ou valor fixo.",
        ),
        migrations.AlterModelTableComment(
            name="respostaanamnese",
            table_comment="Respostas de um cliente a um formulario de anamnese, via link com token.",
        ),
        migrations.AlterModelTableComment(
            name="usuario",
            table_comment="Conta da equipe (painel). papel = ADMIN/PROFISSIONAL/RECEPCAO; 2FA por regra de settings.",
        ),
        migrations.AlterModelTableComment(
            name="versaotermo",
            table_comment="Versao de termo (LGPD, SAUDE ou de procedimento). Uma ativa por escopo; imutavel depois de aceita.",
        ),
        migrations.RemoveIndex(
            model_name="consumosessao",
            name="idx_sessao_pct_cli",
        ),
        migrations.RemoveIndex(
            model_name="listaespera",
            name="idx_espera_cliente",
        ),
        migrations.RemoveIndex(
            model_name="preco",
            name="idx_preco_lookup",
        ),
        migrations.RemoveIndex(
            model_name="respostaanamnese",
            name="idx_anamnese_resp_atend",
        ),
        migrations.AlterUniqueTogether(
            name="habilitacao",
            unique_together=set(),
        ),
        migrations.AlterUniqueTogether(
            name="itempacote",
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name="aceitetermo",
            name="cliente",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="aceites",
                to="aranha_estetica.cliente",
            ),
        ),
        migrations.AlterField(
            model_name="anotacaosessao",
            name="atendimento",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="anotacoes",
                to="aranha_estetica.atendimento",
            ),
        ),
        migrations.AlterField(
            model_name="atendimento",
            name="cliente",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.RESTRICT,
                to="aranha_estetica.cliente",
            ),
        ),
        migrations.AlterField(
            model_name="atendimento",
            name="profissional",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.RESTRICT,
                to="aranha_estetica.profissional",
            ),
        ),
        migrations.AlterField(
            model_name="comprapacote",
            name="cliente",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pacotes_comprados",
                to="aranha_estetica.cliente",
            ),
        ),
        migrations.AlterField(
            model_name="consumosessao",
            name="compra_pacote",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sessoes_realizadas",
                to="aranha_estetica.comprapacote",
            ),
        ),
        migrations.AlterField(
            model_name="habilitacao",
            name="profissional",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                to="aranha_estetica.profissional",
            ),
        ),
        migrations.AlterField(
            model_name="itempacote",
            name="pacote",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="itens",
                to="aranha_estetica.pacote",
            ),
        ),
        migrations.AlterField(
            model_name="itempacote",
            name="procedimento",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                to="aranha_estetica.procedimento",
            ),
        ),
        migrations.AlterField(
            model_name="listaespera",
            name="procedimento",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                to="aranha_estetica.procedimento",
            ),
        ),
        migrations.AlterField(
            model_name="logauditoria",
            name="registro_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="logauditoria",
            name="usuario",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="movimentocarteira",
            name="carteira",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="movimentos",
                to="aranha_estetica.carteira",
            ),
        ),
        migrations.AlterField(
            model_name="movimentocomissao",
            name="profissional",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="comissoes",
                to="aranha_estetica.profissional",
            ),
        ),
        migrations.AlterField(
            model_name="notificacao",
            name="atendimento",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                to="aranha_estetica.atendimento",
            ),
        ),
        migrations.AlterField(
            model_name="preco",
            name="procedimento",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                to="aranha_estetica.procedimento",
            ),
        ),
        migrations.AlterField(
            model_name="prontuarioversao",
            name="prontuario",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="versoes",
                to="aranha_estetica.prontuario",
            ),
        ),
        migrations.AlterField(
            model_name="regracomissao",
            name="profissional",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="regras_comissao",
                to="aranha_estetica.profissional",
            ),
        ),
        migrations.AlterField(
            model_name="respostaanamnese",
            name="cliente",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.PROTECT,
                to="aranha_estetica.cliente",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="codigootp",
            constraint=models.CheckConstraint(
                condition=models.Q(("canal__in", ["EMAIL", "SMS"])),
                name="chk_codigo_otp_canal",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="codigootp",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("proposito__in", ["AGENDAMENTO", "LOGIN_CLIENTE", "DSAR"])
                ),
                name="chk_codigo_otp_proposito",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="feriado",
            constraint=models.CheckConstraint(
                condition=models.Q(("escopo__in", ["NACIONAL", "LOCAL", "CLINICA"])),
                name="chk_feriado_escopo",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="formularioanamnese",
            constraint=models.CheckConstraint(
                condition=models.Q(("tipo__in", ["ANAMNESE", "PESQUISA"])),
                name="chk_formulario_anamnese_tipo",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="formularioanamnese",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "escopo__in",
                        ["GLOBAL", "CATEGORIA", "PROCEDIMENTO", "MODALIDADE"],
                    )
                ),
                name="chk_formulario_anamnese_escopo",
            ),
        ),
        migrations.AddConstraint(
            model_name="habilitacao",
            constraint=models.UniqueConstraint(
                fields=("profissional", "procedimento"),
                name="uniq_habilitacao_profissional_procedimento",
            ),
        ),
        migrations.AddConstraint(
            model_name="itempacote",
            constraint=models.UniqueConstraint(
                fields=("pacote", "procedimento"),
                name="uniq_item_pacote_pacote_procedimento",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="movimentocarteira",
            constraint=models.CheckConstraint(
                condition=models.Q(("tipo__in", ["CREDITO", "DEBITO"])),
                name="chk_movimento_carteira_tipo",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="movimentocarteira",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "origem__in",
                        [
                            "VALE_PRESENTE",
                            "CANCELAMENTO",
                            "AJUSTE_MANUAL",
                            "USO_AGENDAMENTO",
                            "CASHBACK_INDICACAO",
                            "CASHBACK_ESTORNO",
                            "OUTRO",
                        ],
                    )
                ),
                name="chk_movimento_carteira_origem",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="movimentocomissao",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["PENDENTE", "PAGA", "ESTORNADA"])),
                name="chk_movimento_comissao_status",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="notificacao",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "tipo__in",
                        [
                            "LEMBRETE",
                            "LEMBRETE_2H",
                            "CONFIRMACAO",
                            "CANCELAMENTO",
                            "NPS",
                            "PESQUISA",
                            "APROVACAO",
                            "TERMO",
                        ],
                    )
                ),
                name="chk_notificacao_tipo",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="notificacao",
            constraint=models.CheckConstraint(
                condition=models.Q(("canal__in", ["WHATSAPP", "SMS", "EMAIL"])),
                name="chk_notificacao_canal",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="notificacao",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["PENDENTE", "ENVIADO", "FALHOU"])),
                name="chk_notificacao_status",
            ),
        ),
        AdicionarCheckNaoValidado(
            model_name="notificacao",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("resposta__isnull", True),
                    ("resposta__in", ["CONFIRMOU", "CANCELOU"]),
                    _connector="OR",
                ),
                name="chk_notificacao_resposta",
            ),
        ),
        migrations.RunPython(renomear_objetos_pg, desfazer_objetos_pg),
    ]
