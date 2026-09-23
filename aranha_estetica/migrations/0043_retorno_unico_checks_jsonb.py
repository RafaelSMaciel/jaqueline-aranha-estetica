# Pendencias da remodelagem v2.1 (spec §6 / ARCHITECTURE "corretiva"):
#  - uniq_retorno_por_origem: no maximo 1 retorno vivo (ou realizado) por
#    atendimento de origem — idempotencia do F-RET garantida no banco.
#    Pre-limpeza: retorno PENDENTE duplicado (nunca aprovado) vira CANCELADO;
#    duplicata ja aprovada/realizada aborta com a lista de ids (acao manual).
#  - CHECK jsonb_typeof (PG-only, NOT VALID): novas escritas precisam ser
#    objeto/lista; linhas antigas nao sao reescritas. Depois de inspecionar:
#    ALTER TABLE <t> VALIDATE CONSTRAINT <nome>.
# Trigger de atualizado_em ficou de fora (cosmetico; saves com update_fields
# incluem atualizado_em).

from django.db import migrations, models

STATUS_RETORNO_VIVO = ['PENDENTE', 'AGENDADO', 'CONFIRMADO', 'REALIZADO']
_PRIORIDADE = {'REALIZADO': 0, 'CONFIRMADO': 1, 'AGENDADO': 2, 'PENDENTE': 3}

CHECKS_JSONB = (
    # (tabela, coluna, jsonb_typeof esperado, nome)
    ('prontuario', 'respostas_extras', 'object', 'chk_prontuario_extras_objeto'),
    ('resposta_anamnese', 'respostas_json', 'object', 'chk_resposta_anamnese_objeto'),
    ('formulario_anamnese', 'schema_json', 'array', 'chk_formulario_schema_lista'),
)


def limpar_retornos_duplicados(apps, schema_editor):
    from django.utils import timezone

    Atendimento = apps.get_model('aranha_estetica', 'Atendimento')
    LogAuditoria = apps.get_model('aranha_estetica', 'LogAuditoria')

    grupos = {}
    qs = (
        Atendimento.objects
        .filter(eh_retorno=True, status__in=STATUS_RETORNO_VIVO, atendimento_origem__isnull=False)
        .order_by('pk')
        .values('pk', 'atendimento_origem_id', 'status')
    )
    for a in qs:
        grupos.setdefault(a['atendimento_origem_id'], []).append(a)

    agora = timezone.now()
    sem_solucao = []
    for origem_id, itens in grupos.items():
        if len(itens) < 2:
            continue
        itens.sort(key=lambda a: (_PRIORIDADE[a['status']], a['pk']))
        mantido = itens[0]['pk']
        for extra in itens[1:]:
            if extra['status'] != 'PENDENTE':
                sem_solucao.append((origem_id, extra['pk'], extra['status']))
                continue
            Atendimento.objects.filter(pk=extra['pk']).update(status='CANCELADO', atualizado_em=agora)
            LogAuditoria.objects.create(
                acao='migration 0043: retorno PENDENTE duplicado cancelado',
                tabela='atendimento',
                registro_id=extra['pk'],
                detalhes={'atendimento_origem': origem_id, 'retorno_mantido': mantido},
            )
    if sem_solucao:
        raise RuntimeError(
            '0043: retornos duplicados ja aprovados/realizados exigem acao manual '
            f'(origem, retorno, status): {sem_solucao}'
        )
    if schema_editor.connection.vendor == 'postgresql':
        # UPDATEs acima nao podem deixar trigger events pendentes p/ o CREATE INDEX
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def aplicar_checks_jsonb_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for tabela, coluna, tipo, nome in CHECKS_JSONB:
        schema_editor.execute(f'ALTER TABLE {tabela} DROP CONSTRAINT IF EXISTS {nome}', None)
        schema_editor.execute(
            f"ALTER TABLE {tabela} ADD CONSTRAINT {nome} "
            f"CHECK (jsonb_typeof({coluna}) = '{tipo}') NOT VALID",
            None,
        )


def remover_checks_jsonb_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for tabela, _coluna, _tipo, nome in CHECKS_JSONB:
        schema_editor.execute(f'ALTER TABLE {tabela} DROP CONSTRAINT IF EXISTS {nome}', None)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0042_desativar_contas_demo'),
    ]

    operations = [
        migrations.RunPython(limpar_retornos_duplicados, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='atendimento',
            constraint=models.UniqueConstraint(
                condition=models.Q(('eh_retorno', True), ('status__in', STATUS_RETORNO_VIVO)),
                fields=('atendimento_origem',),
                name='uniq_retorno_por_origem',
            ),
        ),
        migrations.RunPython(aplicar_checks_jsonb_pg, remover_checks_jsonb_pg),
    ]
