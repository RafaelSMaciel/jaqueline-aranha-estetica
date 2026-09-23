# Remodelagem v2.1 — Fase 4 (docs/specs/remodelagem-banco-v2.md secao 6.1)
#
# A invariante central do negocio — "um profissional nao atende dois clientes
# ao mesmo tempo" — sai da aplicacao (4 copias divergentes de check-then-insert
# com phantom read) e vira lei no Postgres:
#
#   EXCLUDE USING gist (profissional_id WITH =,
#                       tstzrange(data_hora_inicio, data_hora_fim) WITH &&)
#   WHERE status IN ('PENDENTE','AGENDADO','CONFIRMADO')
#   DEFERRABLE INITIALLY IMMEDIATE
#
# Decisao de design: constraint por EXPRESSAO sobre as colunas existentes
# (data_hora_inicio/fim), NAO um campo range novo — zero query do codebase
# muda, ganho de integridade integral. DEFERRABLE permite swap atomico de
# horarios num reagendamento (SET CONSTRAINTS ... DEFERRED na transacao).
#
# Postgres-only via RunPython: SQLite (testes) nao tem GiST/EXCLUDE.
# O cache-lock e os checks de aplicacao continuam como otimizacao de UX;
# a garantia agora e do banco (IntegrityError 23P01 = horario ja reservado).

from django.db import migrations

STATUS_ATIVOS = ('PENDENTE', 'AGENDADO', 'CONFIRMADO')


def _pares_sobrepostos(Atendimento):
    """Pares (a, b) do mesmo profissional, ambos ativos e com intervalo
    sobreposto — exatamente o que o EXCLUDE recusaria. Portavel (ORM)."""
    ativos = list(
        Atendimento.objects.filter(status__in=STATUS_ATIVOS)
        .order_by('profissional_id', 'data_hora_inicio', 'pk')
        .values('pk', 'profissional_id', 'data_hora_inicio', 'data_hora_fim',
                'status', 'eh_retorno')
    )
    pares = []
    for i, a in enumerate(ativos):
        for b in ativos[i + 1:]:
            if b['profissional_id'] != a['profissional_id'] or b['data_hora_inicio'] >= a['data_hora_fim']:
                break
            pares.append((a, b))
    return pares


def resolver_sobreposicoes(apps, schema_editor):
    """EXCLUDE nao aceita NOT VALID: sobreposicao legada aborta o ALTER.

    Resolve so o caso seguro (sem perder agendamento de cliente em silencio):
    solicitacao PENDENTE nunca aprovada que ja terminou, ou retorno PENDENTE
    sugerido pelo sistema (RetornoService antigo nao checava a agenda) ->
    CANCELADO, com LogAuditoria. O resto aborta com os ids p/ acao manual.
    """
    from django.utils import timezone

    Atendimento = apps.get_model('aranha_estetica', 'Atendimento')
    LogAuditoria = apps.get_model('aranha_estetica', 'LogAuditoria')
    agora = timezone.now()

    def descartavel(a):
        return a['status'] == 'PENDENTE' and (a['eh_retorno'] or a['data_hora_fim'] < agora)

    cancelados = set()
    for a, b in _pares_sobrepostos(Atendimento):
        if a['pk'] in cancelados or b['pk'] in cancelados:
            continue
        alvo = next((x for x in (b, a) if descartavel(x)), None)
        if alvo is None:
            continue
        Atendimento.objects.filter(pk=alvo['pk']).update(status='CANCELADO', atualizado_em=agora)
        cancelados.add(alvo['pk'])
        LogAuditoria.objects.create(
            acao='migration 0035: PENDENTE sobreposto cancelado (pre-EXCLUDE)',
            tabela='atendimento',
            registro_id=alvo['pk'],
            detalhes={'conflitava_com': a['pk'] if alvo is b else b['pk']},
        )

    restantes = [(a['pk'], b['pk']) for a, b in _pares_sobrepostos(Atendimento)]
    if restantes:
        raise RuntimeError(
            '0035: atendimentos ativos sobrepostos do mesmo profissional exigem '
            f'acao manual (cancelar/reagendar um de cada par): {restantes}'
        )
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def aplicar_exclusion_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('CREATE EXTENSION IF NOT EXISTS btree_gist')
    schema_editor.execute(
        "ALTER TABLE atendimento ADD CONSTRAINT excl_atendimento_sobreposicao "
        "EXCLUDE USING gist ("
        "  profissional_id WITH =, "
        "  tstzrange(data_hora_inicio, data_hora_fim) WITH &&"
        ") WHERE (status IN ('PENDENTE', 'AGENDADO', 'CONFIRMADO')) "
        "DEFERRABLE INITIALLY IMMEDIATE"
    )


def remover_exclusion_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        'ALTER TABLE atendimento DROP CONSTRAINT IF EXISTS excl_atendimento_sobreposicao'
    )


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0034_remodelagem_fase3_constraints'),
    ]

    operations = [
        migrations.RunPython(resolver_sobreposicoes, migrations.RunPython.noop),
        migrations.RunPython(aplicar_exclusion_pg, remover_exclusion_pg),
    ]
