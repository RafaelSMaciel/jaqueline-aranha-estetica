# Padrao SQL — fase (e): pacote congelado na venda (docs/specs/padrao-sql.md §2.8, §4.e, §6)
#
# Antes: a compra so apontava p/ o pacote do CATALOGO e saldo/debito/finalizacao/comissao liam
# pacote.itens na hora (por isso a tela travava a edicao de pacote vendido). Agora a venda
# congela o que foi vendido:
#   compra_pacote.pacote_nome            <- pacote.nome no momento da venda
#   compra_pacote_item (1 por item)      <- procedimento, procedimento_nome, qtd_sessoes e
#                                           valor_unitario = round(valor_pago / total de sessoes, 2)
#                                           (a mesma formula da comissao de sessao de pacote)
#   consumo_sessao.compra_pacote_item_id <- o item do MESMO procedimento do atendimento
#
# Dados existentes (RunPython): o snapshot herda o catalogo ATUAL (a trava da UI congelava pacote
# vendido desde a auditoria; antes disso nao ha como saber o que mudou — risco anotado no spec
# §7.2). Consumo sem item do mesmo procedimento (item removido do catalogo) -> RECONSTROI um item
# com qtd = no de consumos daquele procedimento e valor_unitario = 0, com LogAuditoria
# 'migration 0052: item reconstruido'. Consumo acima da quantidade (debito a mais de antes da
# trava) -> so LogAuditoria 'migration 0052: consumo acima da quantidade' (o trigger so vale p/
# INSERT novo). Nada aborta.
#
# Ordem: AddField pacote_nome (nulo) -> CreateModel CompraPacoteItem -> AddField
# consumo_sessao.compra_pacote_item (nulo) + indice -> RunPython (snapshot, reconstrucao, logs;
# termina com SET CONSTRAINTS ALL IMMEDIATE no PG) -> AlterField NOT NULL x2 -> PadronizarNomes
# (compra_pacote_item) + PadronizarFks (consumo_sessao.compra_pacote_item_id) -> PG:
# fk_consumo_sessao_compra_pacote_item_compra (FK composta: o item e da mesma compra),
# fn_consumo_sessao_validar + tg_consumo_sessao_validar (item da compra, mesmo procedimento,
# nao-retorno, saldo), fn_bloquear_mutacao + tg_compra_pacote_item_imutavel (BEFORE UPDATE).
#
# Reverso (dev/testes; rollback de producao = restaurar o dump): derruba os objetos PG e remove
# itens e colunas — sem perda: o catalogo continua la (o codigo antigo volta a ler pacote.itens).

from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal

import django.db.models.deletion
import django.db.models.functions.datetime
import django.utils.timezone
from django.db import migrations, models

import aranha_estetica.db.indices
from aranha_estetica.db.operacoes import PadronizarFks, PadronizarNomes

APP = 'aranha_estetica'
CENTAVO = Decimal('0.01')

SQL_PG = [
    # generica (o 0054 so troca o trigger do movimento_carteira p/ ela: CREATE OR REPLACE)
    """
CREATE OR REPLACE FUNCTION fn_bloquear_mutacao() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% e append-only: % bloqueado', TG_TABLE_NAME, TG_OP
        USING DETAIL = TG_TABLE_NAME || '.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
END $$;
""",
    'CREATE TRIGGER tg_compra_pacote_item_imutavel BEFORE UPDATE ON compra_pacote_item '
    'FOR EACH ROW EXECUTE FUNCTION fn_bloquear_mutacao()',
    """
CREATE OR REPLACE FUNCTION fn_consumo_sessao_validar() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_item_compra    bigint;
    v_item_proc      bigint;
    v_item_qtd       smallint;
    v_atend_proc     bigint;
    v_atend_retorno  boolean;
    v_usadas         integer;
BEGIN
    SELECT compra_pacote_id, procedimento_id, qtd_sessoes
      INTO v_item_compra, v_item_proc, v_item_qtd
      FROM compra_pacote_item WHERE id = NEW.compra_pacote_item_id
       FOR UPDATE;                                -- serializa debitos concorrentes do mesmo item
    SELECT procedimento_id, eh_retorno INTO v_atend_proc, v_atend_retorno
      FROM atendimento WHERE id = NEW.atendimento_id;
    IF v_item_compra IS NULL OR v_atend_proc IS NULL THEN
        RETURN NEW;                               -- a FK (deferida) acusa
    END IF;
    IF v_item_compra <> NEW.compra_pacote_id THEN
        RAISE EXCEPTION 'consumo_sessao: item % nao pertence a compra %', NEW.compra_pacote_item_id, NEW.compra_pacote_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF v_atend_retorno THEN
        RAISE EXCEPTION 'consumo_sessao: retorno gratuito nao consome sessao de pacote (atendimento %)', NEW.atendimento_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_atend_proc <> v_item_proc THEN
        RAISE EXCEPTION 'consumo_sessao: procedimento do atendimento % difere do item %', NEW.atendimento_id, NEW.compra_pacote_item_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*) INTO v_usadas FROM consumo_sessao WHERE compra_pacote_item_id = NEW.compra_pacote_item_id;
    IF v_usadas >= v_item_qtd THEN
        RAISE EXCEPTION 'consumo_sessao: item % sem saldo (% de %)', NEW.compra_pacote_item_id, v_usadas, v_item_qtd
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
""",
    'CREATE TRIGGER tg_consumo_sessao_validar BEFORE INSERT ON consumo_sessao '
    'FOR EACH ROW EXECUTE FUNCTION fn_consumo_sessao_validar()',
    'ALTER TABLE consumo_sessao ADD CONSTRAINT fk_consumo_sessao_compra_pacote_item_compra '
    'FOREIGN KEY (compra_pacote_item_id, compra_pacote_id) REFERENCES compra_pacote_item (id, compra_pacote_id) '
    'DEFERRABLE INITIALLY DEFERRED',
]
SQL_PG_REVERSO = [
    'ALTER TABLE consumo_sessao DROP CONSTRAINT IF EXISTS fk_consumo_sessao_compra_pacote_item_compra',
    'DROP TRIGGER IF EXISTS tg_consumo_sessao_validar ON consumo_sessao',
    'DROP FUNCTION IF EXISTS fn_consumo_sessao_validar()',
    'DROP TRIGGER IF EXISTS tg_compra_pacote_item_imutavel ON compra_pacote_item',
    'DROP FUNCTION IF EXISTS fn_bloquear_mutacao()',
]


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def _rateio(valor_pago, total_sessoes):
    # = models.pacotes.rateio_por_sessao @ 0052 (= ComissaoService.base_calculo @ 0051)
    if not total_sessoes:
        return Decimal('0.00')
    return (Decimal(valor_pago) / Decimal(total_sessoes)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def congelar_compras(apps, schema_editor):
    Pacote = apps.get_model(APP, 'Pacote')
    ItemPacote = apps.get_model(APP, 'ItemPacote')
    Procedimento = apps.get_model(APP, 'Procedimento')
    CompraPacote = apps.get_model(APP, 'CompraPacote')
    CompraPacoteItem = apps.get_model(APP, 'CompraPacoteItem')
    ConsumoSessao = apps.get_model(APP, 'ConsumoSessao')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    compras = list(CompraPacote.objects.order_by('pk'))
    if not compras:
        return
    nome_pacote = dict(Pacote.objects.values_list('pk', 'nome'))
    nome_proc = dict(Procedimento.objects.values_list('pk', 'nome'))
    catalogo = defaultdict(list)  # pacote_id -> [(procedimento_id, qtd)] na ordem do catalogo
    for pacote_id, proc_id, qtd in ItemPacote.objects.order_by('pk').values_list(
        'pacote_id', 'procedimento_id', 'quantidade_sessoes',
    ):
        catalogo[pacote_id].append((proc_id, qtd))
    consumos = defaultdict(list)  # compra_id -> [(consumo_id, procedimento do atendimento)]
    for consumo_id, compra_id, proc_id in ConsumoSessao.objects.order_by('pk').values_list(
        'pk', 'compra_pacote_id', 'atendimento__procedimento_id',
    ):
        consumos[compra_id].append((consumo_id, proc_id))

    for compra in compras:
        compra.pacote_nome = nome_pacote[compra.pacote_id]
    CompraPacote.objects.bulk_update(compras, ['pacote_nome'], batch_size=500)

    novos, reconstruidos = [], []
    for compra in compras:
        itens = catalogo.get(compra.pacote_id, [])
        unitario = _rateio(compra.valor_pago, sum(qtd for _p, qtd in itens))
        for proc_id, qtd in itens:
            novos.append(CompraPacoteItem(
                compra_pacote_id=compra.pk, procedimento_id=proc_id,
                procedimento_nome=nome_proc[proc_id][:100], quantidade_sessoes=qtd, valor_unitario=unitario,
            ))
        do_catalogo = {proc_id for proc_id, _q in itens}
        sem_item = Counter(proc_id for _c, proc_id in consumos[compra.pk] if proc_id not in do_catalogo)
        for proc_id, qtd in sorted(sem_item.items()):
            novos.append(CompraPacoteItem(
                compra_pacote_id=compra.pk, procedimento_id=proc_id,
                procedimento_nome=nome_proc[proc_id][:100], quantidade_sessoes=qtd,
                valor_unitario=Decimal('0.00'),
            ))
            reconstruidos.append((compra.pk, proc_id, qtd))
    CompraPacoteItem.objects.bulk_create(novos, batch_size=500)

    item_de = {
        (compra_id, proc_id): (item_id, qtd)
        for item_id, compra_id, proc_id, qtd in CompraPacoteItem.objects.values_list(
            'pk', 'compra_pacote_id', 'procedimento_id', 'quantidade_sessoes',
        )
    }
    por_item = defaultdict(list)
    lista = list(ConsumoSessao.objects.order_by('pk').select_related('atendimento'))
    for consumo in lista:
        item_id, _qtd = item_de[(consumo.compra_pacote_id, consumo.atendimento.procedimento_id)]
        consumo.compra_pacote_item_id = item_id
        por_item[(consumo.compra_pacote_id, consumo.atendimento.procedimento_id)].append(consumo.pk)
    ConsumoSessao.objects.bulk_update(lista, ['compra_pacote_item'], batch_size=500)

    for compra_id, proc_id, qtd in reconstruidos:
        item_id, _qtd = item_de[(compra_id, proc_id)]
        LogAuditoria.objects.create(
            acao='migration 0052: item reconstruido', tabela='compra_pacote_item', registro_id=item_id,
            detalhes={'compra_pacote': compra_id, 'procedimento': proc_id, 'qtd_sessoes': qtd,
                      'valor_unitario': '0.00',
                      'motivo': 'consumo sem item do mesmo procedimento no catalogo atual do pacote'},
        )
    acima = []
    for chave, ids in sorted(por_item.items()):
        item_id, qtd = item_de[chave]
        if len(ids) > qtd:
            acima.append(item_id)
            LogAuditoria.objects.create(
                acao='migration 0052: consumo acima da quantidade', tabela='compra_pacote_item',
                registro_id=item_id,
                detalhes={'compra_pacote': chave[0], 'procedimento': chave[1], 'qtd_sessoes': qtd,
                          'consumos': len(ids), 'consumo_ids': ids},
            )
    LogAuditoria.objects.create(
        acao='migration 0052: pacote congelado na venda (compra_pacote_item)', tabela='compra_pacote_item',
        detalhes={'compras': len(compras), 'itens': len(novos), 'consumos': len(lista),
                  'itens_reconstruidos': len(reconstruidos), 'itens_com_consumo_acima': len(acima)},
    )
    if _pg(schema_editor):
        # FKs DEFERRABLE: dispara os checks agora (o ALTER TABLE seguinte nao pode
        # ver trigger events pendentes — padrao 0034-0051)
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def aplicar_objetos_pg(apps, schema_editor):
    if not _pg(schema_editor):
        return
    for sql in SQL_PG:
        schema_editor.execute(sql, None)


def remover_objetos_pg(apps, schema_editor):
    if not _pg(schema_editor):
        return
    for sql in SQL_PG_REVERSO:
        schema_editor.execute(sql, None)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0051_formulario_prontuario'),
    ]

    operations = [
        migrations.AddField(
            model_name='comprapacote',
            name='pacote_nome',
            field=models.CharField(max_length=150, null=True),
        ),
        migrations.CreateModel(
            name='CompraPacoteItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('procedimento_nome', models.CharField(db_comment='Nome do procedimento no momento da venda.', max_length=100)),
                ('quantidade_sessoes', models.SmallIntegerField(db_column='qtd_sessoes')),
                ('valor_unitario', models.DecimalField(db_comment='Rateio do valor pago por sessao: round(valor_pago / total de sessoes da compra, 2) - base da comissao da sessao de pacote (D20). 0 = item reconstruido pela 0052.', decimal_places=2, max_digits=10)),
                ('criado_em', models.DateTimeField(db_default=django.db.models.functions.datetime.Now(), default=django.utils.timezone.now, editable=False)),
                ('compra_pacote', models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.CASCADE, related_name='itens', to='aranha_estetica.comprapacote')),
                ('procedimento', models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='itens_compra_pacote', to='aranha_estetica.procedimento')),
            ],
            options={
                'db_table': 'compra_pacote_item',
                'db_table_comment': 'Itens congelados na venda (snapshot). Saldo = qtd_sessoes - consumos do item (fn_saldo_pacote). Imutavel (tg_compra_pacote_item_imutavel).',
                'ordering': ('id',),
                'indexes': [
                    aranha_estetica.db.indices.Indice(fields=['procedimento'], name='ix_compra_pacote_item_procedimento'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('compra_pacote', 'procedimento'), name='uq_compra_pacote_item_compra_procedimento'),
                    models.UniqueConstraint(fields=('id', 'compra_pacote'), name='uq_compra_pacote_item_id_compra'),
                    models.CheckConstraint(condition=models.Q(('quantidade_sessoes__gt', 0)), name='ck_compra_pacote_item_qtd_sessoes_positiva'),
                    models.CheckConstraint(condition=models.Q(('valor_unitario__gte', 0)), name='ck_compra_pacote_item_valor_unitario_nao_negativo'),
                ],
            },
        ),
        migrations.AddField(
            model_name='consumosessao',
            name='compra_pacote_item',
            field=models.ForeignKey(db_index=False, null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='consumos', to='aranha_estetica.comprapacoteitem'),
        ),
        migrations.AddIndex(
            model_name='consumosessao',
            index=aranha_estetica.db.indices.Indice(fields=['compra_pacote_item'], name='ix_consumo_sessao_compra_pacote_item'),
        ),
        migrations.RunPython(congelar_compras, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='comprapacote',
            name='pacote_nome',
            field=models.CharField(db_comment='Nome do pacote no momento da venda (o catalogo pode mudar depois).', max_length=150),
        ),
        migrations.AlterField(
            model_name='consumosessao',
            name='compra_pacote_item',
            field=models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.RESTRICT, related_name='consumos', to='aranha_estetica.comprapacoteitem'),
        ),
        PadronizarNomes(tabelas=['compra_pacote_item']),
        PadronizarFks(tabela='consumo_sessao', colunas=['compra_pacote_item_id']),
        migrations.RunPython(aplicar_objetos_pg, remover_objetos_pg),
    ]
