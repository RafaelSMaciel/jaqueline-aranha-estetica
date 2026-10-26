# Padrao SQL — fase (f): comissao como livro-razao (docs/specs/padrao-sql.md §2.9, §4.f, §6)
#
# Antes: 1 linha por atendimento com status PENDENTE -> PAGA | ESTORNADA mudado por UPDATE (por
# isso a 0038 deixou movimento_comissao fora da imutabilidade). Agora o ledger e append-only:
#   CREDITO   (atendimento realizado; 1 por atendimento, mesmo depois de estornado)
#   PAGAMENTO (baixa no painel; referencia o CREDITO; autor)
#   ESTORNO   (atendimento cancelado; referencia o CREDITO; pago ou nao)
# Status e saldo passam a ser DERIVADOS; o saldo fica negativo quando uma comissao ja paga e
# estornada (o profissional devolve no proximo repasse) — antes isso sumia no PAGA->ESTORNADA.
#
# Dados (RunPython; cada linha atual continua sendo o CREDITO, mesmo id):
#   PENDENTE                -> CREDITO
#   PAGA                    -> CREDITO + PAGAMENTO(criado_em = pago_em, 'migração 0053')
#   ESTORNADA com pago_em   -> CREDITO + PAGAMENTO(pago_em) + ESTORNO(data estimada)
#   ESTORNADA sem pago_em   -> CREDITO + ESTORNO(data estimada)
# Data estimada (o sistema nunca gravou quando estornou) = atendimento.atualizado_em (ou o
# criado_em do credito), nunca antes do credito nem do pagamento; observacao 'migração 0053: data
# estimada'. PAGA sem pago_em (dado sujo) -> PAGAMENTO na data do credito, tambem 'data estimada'.
#
# Aborta (lista os ids; nada aplicado) se: valor <= 0 (o validator aceitava 0; o servico nunca
# cria), 2+ linhas do mesmo atendimento, status fora de PENDENTE/PAGA/ESTORNADA. Pre-checagem p/
# o runbook (schema 0026, mesmos nomes de tabela/coluna):
#   SELECT id, atendimento_id, valor, status FROM movimento_comissao
#    WHERE valor <= 0 OR status NOT IN ('PENDENTE', 'PAGA', 'ESTORNADA');
#   SELECT atendimento_id, array_agg(id ORDER BY id) FROM movimento_comissao
#    GROUP BY atendimento_id HAVING count(*) > 1;
#
# Achado §7.3 item 1 (LGPD): FidelidadeService gravava o NOME da indicada em
# movimento_carteira.observacoes ('Indicacao: <nome> (cli #<id>)'), que e imutavel por trigger
# (0038) — o esquecimento nunca o apagava. O servico agora grava so o id; aqui as linhas antigas
# viram 'Indicacao (cli #<id>)' derrubando e recriando trg_movimento_carteira_imutavel no MESMO
# passo (o rename p/ tg_/fn_bloquear_mutacao e da 0054). Irreversivel de proposito (reverso no-op).
#
# Ordem: saneamento da carteira -> pre-checagem -> RemoveConstraint
# uniq_comissao_ativa_por_atendimento -> AlterField criado_em (default/db_default now(): o
# auto_now_add sobrescreveria a data do PAGAMENTO migrado) -> AddField tipo/movimento_origem/
# usuario_autor -> RunPython (PAGAMENTO/ESTORNO; SET CONSTRAINTS ALL IMMEDIATE) -> RemoveIndex
# idx_comissao_prof_status -> RemoveField status/pago_em -> estado: regra PROTECT (regra usada nao
# se exclui: o SET NULL bateria no trigger) -> AddConstraint (CHECK/UNIQUE parciais) + AddIndex
# -> PadronizarFks (FKs novas) -> PG: fn_movimento_comissao_validar + tg_movimento_comissao_validar,
# tg_movimento_comissao_imutavel (fn_bloquear_mutacao da 0052).
#
# Reverso (dev/testes; rollback de producao = restaurar o dump): derruba os triggers, recria
# status/pago_em a partir do ledger (ESTORNADA > PAGA > PENDENTE; pago_em = data do PAGAMENTO) e
# apaga as linhas que nao sao CREDITO.

import re
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
import django.db.models.functions.datetime
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
from django.db.models import Count

import aranha_estetica.db.indices
from aranha_estetica.db.operacoes import PadronizarFks

APP = 'aranha_estetica'
STATUS = ('PENDENTE', 'PAGA', 'ESTORNADA')
OBS = 'migração 0053'
OBS_ESTIMADA = 'migração 0053: data estimada'
UM_MICROSSEGUNDO = timedelta(microseconds=1)
RE_INDICACAO = re.compile(r'^Indicacao: .* \(cli #(\d+)\)$', re.S)

SQL_FUNCAO_VALIDAR = """
CREATE OR REPLACE FUNCTION fn_movimento_comissao_validar() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
    v_origem movimento_comissao%ROWTYPE;
BEGIN
    IF NEW.tipo = 'CREDITO' THEN
        RETURN NEW;
    END IF;
    SELECT * INTO v_origem FROM movimento_comissao WHERE id = NEW.movimento_comissao_origem_id
       FOR UPDATE;                                -- serializa PAGAMENTO x ESTORNO do mesmo credito
    IF v_origem.id IS NULL THEN
        RETURN NEW;                               -- a FK (deferida) acusa
    END IF;
    IF v_origem.tipo <> 'CREDITO' THEN
        RAISE EXCEPTION 'movimento_comissao: % deve referenciar um CREDITO (origem %)', NEW.tipo, v_origem.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.profissional_id, NEW.atendimento_id) IS DISTINCT FROM (v_origem.profissional_id, v_origem.atendimento_id)
       OR NEW.valor <> v_origem.valor THEN
        RAISE EXCEPTION 'movimento_comissao: % deve repetir profissional, atendimento e valor do credito %', NEW.tipo, v_origem.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.tipo = 'PAGAMENTO' AND EXISTS (
           SELECT 1 FROM movimento_comissao
            WHERE movimento_comissao_origem_id = v_origem.id AND tipo = 'ESTORNO') THEN
        RAISE EXCEPTION 'movimento_comissao: credito % estornado nao pode ser pago', v_origem.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
"""

SQL_PG = [
    SQL_FUNCAO_VALIDAR,
    'CREATE TRIGGER tg_movimento_comissao_validar BEFORE INSERT ON movimento_comissao '
    'FOR EACH ROW EXECUTE FUNCTION fn_movimento_comissao_validar()',
    'CREATE TRIGGER tg_movimento_comissao_imutavel BEFORE UPDATE OR DELETE ON movimento_comissao '
    'FOR EACH ROW EXECUTE FUNCTION fn_bloquear_mutacao()',
]
SQL_PG_REVERSO = [
    'DROP TRIGGER IF EXISTS tg_movimento_comissao_imutavel ON movimento_comissao',
    'DROP TRIGGER IF EXISTS tg_movimento_comissao_validar ON movimento_comissao',
    'DROP FUNCTION IF EXISTS fn_movimento_comissao_validar()',
]

# trigger da 0038 (o rename p/ tg_movimento_carteira_imutavel/fn_bloquear_mutacao e da 0054)
SQL_TRIGGER_CARTEIRA = (
    'CREATE TRIGGER trg_movimento_carteira_imutavel BEFORE UPDATE OR DELETE ON movimento_carteira '
    'FOR EACH ROW EXECUTE FUNCTION bloquear_mutacao_ledger()'
)


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def _escalar(schema_editor, sql, params=None):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


# ─── §7.3 item 1: nome da indicada fora do ledger da carteira ───────────────
def sanear_observacao_cashback(apps, schema_editor):
    MovimentoCarteira = apps.get_model(APP, 'MovimentoCarteira')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    alvo = []
    for pk, texto in MovimentoCarteira.objects.filter(
        origem='CASHBACK_INDICACAO', observacoes__startswith='Indicacao: ',
    ).order_by('pk').values_list('pk', 'observacoes'):
        casou = RE_INDICACAO.match(texto)
        if casou:
            alvo.append((pk, f'Indicacao (cli #{casou.group(1)})'))
    if not alvo:
        return
    pg = _pg(schema_editor)
    tinha_trigger = pg and _escalar(
        schema_editor,
        "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_movimento_carteira_imutavel' "
        "AND tgrelid = 'movimento_carteira'::regclass",
    ) > 0
    if tinha_trigger:
        schema_editor.execute('DROP TRIGGER trg_movimento_carteira_imutavel ON movimento_carteira', None)
    for pk, texto in alvo:
        MovimentoCarteira.objects.filter(pk=pk).update(observacoes=texto)
    if tinha_trigger:
        schema_editor.execute(SQL_TRIGGER_CARTEIRA, None)
    LogAuditoria.objects.create(
        acao='migration 0053: nome da indicada retirado do ledger da carteira (LGPD)',
        tabela='movimento_carteira',
        detalhes={'movimentos': len(alvo), 'ids': [pk for pk, _t in alvo]},
    )
    if pg:
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


# ─── comissao: pre-checagem ─────────────────────────────────────────────────
def pre_checar_comissoes(apps, schema_editor):
    MovimentoComissao = apps.get_model(APP, 'MovimentoComissao')
    problemas = []
    for m in MovimentoComissao.objects.order_by('pk').values('pk', 'valor', 'status'):
        if m['valor'] is None or m['valor'] <= 0:
            problemas.append(f"id={m['pk']} valor={m['valor']} (deve ser > 0)")
        if m['status'] not in STATUS:
            problemas.append(f"id={m['pk']} status={m['status']!r} fora do dominio")
    repetidos = (
        MovimentoComissao.objects.order_by().values('atendimento_id')
        .annotate(n=Count('id')).filter(n__gt=1).values_list('atendimento_id', flat=True)
    )
    for atendimento_id in sorted(repetidos):
        ids = list(MovimentoComissao.objects.filter(atendimento_id=atendimento_id)
                   .order_by('pk').values_list('pk', flat=True))
        problemas += [f'id={i} atendimento={atendimento_id} com {len(ids)} comissoes' for i in ids]
    if problemas:
        raise RuntimeError(
            '0053 (comissao como livro-razao) abortada — corrija as comissoes e rode de novo '
            '(pre-checagem SQL no runbook, docs/specs/padrao-sql.md §4.f): ' + ' | '.join(problemas)
        )


# ─── comissao: status -> lancamentos ────────────────────────────────────────
def migrar_ledger(apps, schema_editor):
    MovimentoComissao = apps.get_model(APP, 'MovimentoComissao')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    linhas = list(MovimentoComissao.objects.order_by('pk').values(
        'pk', 'profissional_id', 'atendimento_id', 'valor', 'status', 'pago_em', 'criado_em',
        'atendimento__atualizado_em',
    ))
    if not linhas:
        return
    novos = []
    contagem = Counter()
    for c in linhas:
        contagem[c['status']] += 1
        base = {'profissional_id': c['profissional_id'], 'atendimento_id': c['atendimento_id'],
                'valor': c['valor'], 'movimento_origem_id': c['pk'], 'status': 'PENDENTE'}
        pago_em = None
        if c['status'] == 'PAGA' or (c['status'] == 'ESTORNADA' and c['pago_em'] is not None):
            pago_em, obs = (c['pago_em'], OBS) if c['pago_em'] else (c['criado_em'], OBS_ESTIMADA)
            novos.append(MovimentoComissao(tipo='PAGAMENTO', criado_em=pago_em, observacoes=obs, **base))
            contagem['pagamentos'] += 1
            contagem['datas_estimadas'] += obs == OBS_ESTIMADA
        if c['status'] == 'ESTORNADA':
            estorno_em = max(c['atendimento__atualizado_em'] or c['criado_em'], c['criado_em'])
            if pago_em is not None and estorno_em <= pago_em:
                estorno_em = pago_em + UM_MICROSSEGUNDO
            novos.append(MovimentoComissao(tipo='ESTORNO', criado_em=estorno_em, observacoes=OBS_ESTIMADA, **base))
            contagem['estornos'] += 1
            contagem['datas_estimadas'] += 1
            contagem['estornadas_depois_de_pagas'] += pago_em is not None
    MovimentoComissao.objects.bulk_create(novos, batch_size=500)
    LogAuditoria.objects.create(
        acao='migration 0053: comissao como livro-razao (movimento_comissao)',
        tabela='movimento_comissao',
        detalhes={
            'creditos': len(linhas), 'pendentes': contagem['PENDENTE'], 'pagas': contagem['PAGA'],
            'estornadas': contagem['ESTORNADA'], 'pagamentos': contagem['pagamentos'],
            'estornos': contagem['estornos'], 'datas_estimadas': contagem['datas_estimadas'],
            'estornadas_depois_de_pagas': contagem['estornadas_depois_de_pagas'],
        },
    )
    if _pg(schema_editor):
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def restaurar_status(apps, schema_editor):
    """Reverso (dev/testes): status/pago_em a partir do ledger; so os CREDITOs ficam."""
    MovimentoComissao = apps.get_model(APP, 'MovimentoComissao')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    derivados = defaultdict(dict)
    for origem_id, tipo, criado_em in MovimentoComissao.objects.exclude(tipo='CREDITO').values_list(
        'movimento_origem_id', 'tipo', 'criado_em',
    ):
        derivados[origem_id][tipo] = criado_em
    creditos = list(MovimentoComissao.objects.filter(tipo='CREDITO').order_by('pk'))
    for credito in creditos:
        lancamentos = derivados.get(credito.pk, {})
        credito.status = ('ESTORNADA' if 'ESTORNO' in lancamentos
                          else 'PAGA' if 'PAGAMENTO' in lancamentos else 'PENDENTE')
        credito.pago_em = lancamentos.get('PAGAMENTO')
    MovimentoComissao.objects.bulk_update(creditos, ['status', 'pago_em'], batch_size=500)
    apagados, _ = MovimentoComissao.objects.exclude(tipo='CREDITO').delete()
    if creditos:
        LogAuditoria.objects.create(
            acao='migration 0053 (reversa): comissao voltou a status por linha',
            tabela='movimento_comissao', detalhes={'creditos': len(creditos), 'lancamentos_apagados': apagados},
        )
    if _pg(schema_editor):
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
        ('aranha_estetica', '0052_compra_pacote_item'),
    ]

    operations = [
        migrations.RunPython(pre_checar_comissoes, migrations.RunPython.noop),
        migrations.RunPython(sanear_observacao_cashback, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='movimentocomissao',
            name='uniq_comissao_ativa_por_atendimento',
        ),
        migrations.AlterField(
            model_name='movimentocomissao',
            name='criado_em',
            field=models.DateTimeField(db_default=django.db.models.functions.datetime.Now(), default=django.utils.timezone.now, editable=False),
        ),
        migrations.AddField(
            model_name='movimentocomissao',
            name='tipo',
            field=models.CharField(choices=[('CREDITO', 'Crédito (atendimento realizado)'), ('ESTORNO', 'Estorno (cancelamento)'), ('PAGAMENTO', 'Pagamento (baixa)')], default='CREDITO', max_length=10),
        ),
        migrations.AddField(
            model_name='movimentocomissao',
            name='movimento_origem',
            field=models.ForeignKey(blank=True, db_column='movimento_comissao_origem_id', db_comment='CREDITO que o ESTORNO/PAGAMENTO liquida (NULL no proprio CREDITO).', db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='lancamentos_derivados', to='aranha_estetica.movimentocomissao'),
        ),
        migrations.AddField(
            model_name='movimentocomissao',
            name='usuario_autor',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(migrar_ledger, restaurar_status),
        migrations.RemoveIndex(
            model_name='movimentocomissao',
            name='idx_comissao_prof_status',
        ),
        migrations.RemoveField(
            model_name='movimentocomissao',
            name='status',
        ),
        migrations.RemoveField(
            model_name='movimentocomissao',
            name='pago_em',
        ),
        migrations.AlterField(
            model_name='movimentocomissao',
            name='regra',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='aranha_estetica.regracomissao'),
        ),
        migrations.AlterField(
            model_name='movimentocomissao',
            name='valor',
            field=models.DecimalField(db_comment='Sempre positivo; o sinal vem do tipo (CREDITO +, ESTORNO/PAGAMENTO -).', decimal_places=2, max_digits=10, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))]),
        ),
        migrations.AddConstraint(
            model_name='movimentocomissao',
            constraint=models.CheckConstraint(condition=models.Q(('tipo__in', ['CREDITO', 'ESTORNO', 'PAGAMENTO'])), name='ck_movimento_comissao_tipo'),
        ),
        migrations.AddConstraint(
            model_name='movimentocomissao',
            constraint=models.CheckConstraint(condition=models.Q(('valor__gt', 0)), name='ck_movimento_comissao_valor_positivo'),
        ),
        migrations.AddConstraint(
            model_name='movimentocomissao',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('movimento_origem__isnull', True), ('tipo', 'CREDITO')), models.Q(models.Q(('tipo', 'CREDITO'), _negated=True), ('movimento_origem__isnull', False)), _connector='OR'), name='ck_movimento_comissao_origem_coerente'),
        ),
        migrations.AddConstraint(
            model_name='movimentocomissao',
            constraint=models.CheckConstraint(condition=models.Q(('tipo', 'CREDITO'), ('regra__isnull', True), _connector='OR'), name='ck_movimento_comissao_regra_so_credito'),
        ),
        migrations.AddConstraint(
            model_name='movimentocomissao',
            constraint=models.UniqueConstraint(condition=models.Q(('tipo', 'CREDITO')), fields=('atendimento',), name='uq_movimento_comissao_credito_por_atendimento'),
        ),
        migrations.AddConstraint(
            model_name='movimentocomissao',
            constraint=models.UniqueConstraint(condition=models.Q(('tipo', 'ESTORNO')), fields=('movimento_origem',), name='uq_movimento_comissao_estorno_por_origem'),
        ),
        migrations.AddConstraint(
            model_name='movimentocomissao',
            constraint=models.UniqueConstraint(condition=models.Q(('tipo', 'PAGAMENTO')), fields=('movimento_origem',), name='uq_movimento_comissao_pagamento_por_origem'),
        ),
        migrations.AddIndex(
            model_name='movimentocomissao',
            index=aranha_estetica.db.indices.Indice(fields=['profissional', '-criado_em'], name='ix_movimento_comissao_profissional_criado_em'),
        ),
        migrations.AddIndex(
            model_name='movimentocomissao',
            index=aranha_estetica.db.indices.Indice(condition=models.Q(('movimento_origem__isnull', False)), fields=['movimento_origem', 'tipo'], name='ix_movimento_comissao_origem_parcial'),
        ),
        PadronizarFks(tabela='movimento_comissao', colunas=['movimento_comissao_origem_id', 'usuario_autor_id']),
        migrations.RunPython(aplicar_objetos_pg, remover_objetos_pg),
    ]
