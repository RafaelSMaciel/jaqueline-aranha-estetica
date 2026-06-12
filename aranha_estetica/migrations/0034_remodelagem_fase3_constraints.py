# Remodelagem v2.1 — Fase 3 (docs/specs/remodelagem-banco-v2.md secao 6)
# Data migration normaliza telefone/cpf p/ forma canonica (digits-only) e
# deduplica ANTES das UNIQUE parciais. CHECK regex de formato vai por RunSQL
# postgres-only (SQLite dos testes nao tem REGEXP).

import aranha_estetica.validators
import django.db.models.functions.text
from django.db import migrations, models


def _digits(v):
    return ''.join(c for c in (v or '') if c.isdigit())


def normalizar_e_deduplicar(apps, schema_editor):
    from django.utils import timezone
    Cliente = apps.get_model('aranha_estetica', 'Cliente')

    # 1. normaliza
    for c in Cliente.objects.all():
        tel, cpf = _digits(c.telefone), _digits(c.cpf)
        if tel != (c.telefone or '') or cpf != (c.cpf or ''):
            c.telefone = tel or c.telefone
            c.cpf = cpf or None
            c.save(update_fields=['telefone', 'cpf'])

    # 2. dedup telefone entre ativos: mantem o mais recente, soft-deleta o resto
    vistos = {}
    qs = Cliente.objects.filter(
        deletado_em__isnull=True,
    ).exclude(telefone__isnull=True).exclude(telefone='').order_by('-criado_em')
    for c in qs:
        if c.telefone in vistos:
            c.deletado_em = timezone.now()
            c.ativo = False
            c.save(update_fields=['deletado_em', 'ativo'])
        else:
            vistos[c.telefone] = c.pk


def aplicar_checks_regex_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        "ALTER TABLE cliente ADD CONSTRAINT chk_cliente_telefone_digits "
        "CHECK (telefone IS NULL OR telefone = '' OR telefone ~ '^[0-9]{10,11}$')"
    )
    schema_editor.execute(
        "ALTER TABLE cliente ADD CONSTRAINT chk_cliente_cpf_digits "
        "CHECK (cpf IS NULL OR cpf = '' OR cpf ~ '^[0-9]{11}$')"
    )


def remover_checks_regex_pg(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('ALTER TABLE cliente DROP CONSTRAINT IF EXISTS chk_cliente_telefone_digits')
    schema_editor.execute('ALTER TABLE cliente DROP CONSTRAINT IF EXISTS chk_cliente_cpf_digits')


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0033_remodelagem_fase2c_cliente_nome'),
    ]

    operations = [
        migrations.RunPython(normalizar_e_deduplicar, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='cliente',
            name='uniq_cliente_email_ativo',
        ),
        migrations.AlterField(
            model_name='cliente',
            name='cpf',
            field=models.CharField(blank=True, max_length=11, null=True, validators=[aranha_estetica.validators.validate_cpf]),
        ),
        migrations.AddConstraint(
            model_name='carteira',
            constraint=models.CheckConstraint(condition=models.Q(('saldo__gte', 0)), name='chk_carteira_saldo_nao_negativo'),
        ),
        migrations.AddConstraint(
            model_name='cliente',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower('email'), condition=models.Q(('deletado_em__isnull', True), models.Q(('email__isnull', True), _negated=True), models.Q(('email', ''), _negated=True)), name='uniq_cliente_email_ativo'),
        ),
        migrations.AddConstraint(
            model_name='cliente',
            constraint=models.UniqueConstraint(condition=models.Q(('deletado_em__isnull', True), models.Q(('telefone__isnull', True), _negated=True), models.Q(('telefone', ''), _negated=True)), fields=('telefone',), name='uniq_cliente_telefone_ativo'),
        ),
        migrations.AddConstraint(
            model_name='cliente',
            constraint=models.UniqueConstraint(condition=models.Q(('deletado_em__isnull', True), models.Q(('cpf__isnull', True), _negated=True), models.Q(('cpf', ''), _negated=True)), fields=('cpf',), name='uniq_cliente_cpf_ativo'),
        ),
        migrations.AddConstraint(
            model_name='codigootp',
            constraint=models.CheckConstraint(condition=models.Q(('tentativas__lte', 10)), name='chk_otp_tentativas_teto'),
        ),
        migrations.AddConstraint(
            model_name='consumosessao',
            constraint=models.UniqueConstraint(condition=models.Q(('atendimento__isnull', False)), fields=('atendimento',), name='uniq_consumo_por_atendimento'),
        ),
        migrations.AddConstraint(
            model_name='listaespera',
            constraint=models.UniqueConstraint(condition=models.Q(('notificado', False)), fields=('cliente', 'procedimento', 'data_desejada'), name='uniq_espera_ativa'),
        ),
        migrations.AddConstraint(
            model_name='preco',
            constraint=models.UniqueConstraint(fields=('procedimento', 'profissional', 'vigente_desde'), name='uniq_preco_vigencia'),
        ),
        migrations.AddConstraint(
            model_name='preco',
            constraint=models.UniqueConstraint(condition=models.Q(('profissional__isnull', True)), fields=('procedimento', 'vigente_desde'), name='uniq_preco_base_vigencia'),
        ),
        migrations.AddConstraint(
            model_name='promocao',
            constraint=models.CheckConstraint(condition=models.Q(('desconto_percentual__gte', 0), ('desconto_percentual__lte', 100)), name='chk_promocao_desconto_0_100'),
        ),
        migrations.AddConstraint(
            model_name='promocao',
            constraint=models.CheckConstraint(condition=models.Q(('preco_promocional__isnull', True), ('preco_promocional__gte', 0), _connector='OR'), name='chk_promocao_preco_positivo'),
        ),
        migrations.AddConstraint(
            model_name='promocao',
            constraint=models.CheckConstraint(condition=models.Q(('desconto_percentual__gt', 0), ('preco_promocional__isnull', False), _negated=True), name='chk_promocao_desconto_xor_preco'),
        ),
        migrations.AddConstraint(
            model_name='versaotermo',
            constraint=models.UniqueConstraint(condition=models.Q(('ativa', True)), fields=('tipo', 'procedimento'), name='uniq_termo_ativo_por_escopo'),
        ),
        migrations.AddConstraint(
            model_name='versaotermo',
            constraint=models.UniqueConstraint(condition=models.Q(('ativa', True), ('procedimento__isnull', True)), fields=('tipo',), name='uniq_termo_ativo_global'),
        ),
        # CHECK de formato no banco — postgres-only via RunPython
        # (SQLite dos testes nao tem operador regex; RunSQL rodaria nos dois)
        migrations.RunPython(aplicar_checks_regex_pg, remover_checks_regex_pg),
    ]
