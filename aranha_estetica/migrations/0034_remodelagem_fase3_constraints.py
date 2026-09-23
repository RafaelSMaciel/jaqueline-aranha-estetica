# Remodelagem v2.1 — Fase 3 (docs/specs/remodelagem-banco-v2.md secao 6)
# Data migration normaliza telefone/cpf/email p/ forma canonica e deduplica
# ANTES das UNIQUE parciais e CHECKs novos. CHECK regex de formato vai por
# RunPython postgres-only (SQLite dos testes nao tem REGEXP).
#
# Auditoria pre-producao (editada in-place: prod nunca aplicou a 0034):
#  - o UNIQUE global antigo de cpf e o uniq_cliente_email_ativo antigo caem
#    ANTES da normalizacao (senao '529.982.247-25' + '52998224725' colidem);
#  - telefone: remove DDI 55 / zero de tronco; fora de 10-11 digitos vira NULL;
#  - duplicados (telefone, cpf, lower(email)) entre ativos: o cliente com MAIS
#    atendimentos (empate: o mais recente) mantem o valor; os outros ficam
#    ATIVOS com o campo NULL (sem soft-delete: historico continua visivel) e
#    LogAuditoria aponta a duplicata p/ mesclagem manual (valor mascarado);
#  - telefone invalido (ex.: sem DDD) vira NULL e o log guarda tambem os
#    digitos originais (telefone_original) — o valor nao se perde; a
#    anonimizacao LGPD limpa os detalhes desses logs;
#  - termo/preco/lista de espera/promocao: dedup/clamp p/ os UNIQUE/CHECK novos;
#    promocao com valor fora do CHECK (desconto <0/>100, preco <0) e DESATIVADA
#    (150% virava 100% ativa = servico de graca) e o log guarda o original;
#  - 1 UPDATE por linha + SET CONSTRAINTS ALL IMMEDIATE (PG): sem 'pending
#    trigger events' nos ALTER TABLE seguintes.

import aranha_estetica.validators
import django.db.models.functions.text
from django.db import migrations, models


def _digits(v):
    return ''.join(c for c in (v or '') if c.isdigit())


def _tel_canonico(v):
    """Copia congelada de validators.normalizar_telefone + checagem 10-11."""
    d = _digits(v)
    if len(d) in (12, 13) and d.startswith('55'):
        d = d[2:]
    if len(d) in (11, 12) and d.startswith('0'):
        d = d[1:]
    return d if len(d) in (10, 11) else None


def _mascara(campo, valor):
    """LogAuditoria nao duplica PII: guarda so o suficiente p/ reconhecer."""
    valor = valor or ''
    if campo == 'email':
        local, _, dominio = valor.partition('@')
        return f'{local[:2]}***@{dominio}' if dominio else '***'
    d = _digits(valor)
    if campo == 'cpf':
        return f'{d[:3]}*****{d[-2:]}' if len(d) == 11 else '***'
    return f'{d[:2]}****{d[-4:]}' if len(d) >= 6 else '***'


def normalizar_e_deduplicar(apps, schema_editor):
    from django.db.models import Count

    Cliente = apps.get_model('aranha_estetica', 'Cliente')
    Atendimento = apps.get_model('aranha_estetica', 'Atendimento')
    LogAuditoria = apps.get_model('aranha_estetica', 'LogAuditoria')
    VersaoTermo = apps.get_model('aranha_estetica', 'VersaoTermo')
    Preco = apps.get_model('aranha_estetica', 'Preco')
    ListaEspera = apps.get_model('aranha_estetica', 'ListaEspera')
    Promocao = apps.get_model('aranha_estetica', 'Promocao')

    logs = []

    def log(tabela, pk, motivo, **detalhes):
        logs.append(LogAuditoria(
            acao=f'migration 0034: {motivo}', tabela=tabela, registro_id=pk,
            detalhes=detalhes or None,
        ))

    # ── 1. cliente: forma canonica calculada em memoria ──
    n_atend = dict(
        Atendimento.objects.values('cliente_id').annotate(n=Count('id'))
        .values_list('cliente_id', 'n')
    )
    originais, novos, score, ativos = {}, {}, {}, []
    for c in Cliente.objects.order_by('pk').values(
        'pk', 'telefone', 'cpf', 'email', 'criado_em', 'deletado_em',
    ):
        pk = c['pk']
        originais[pk] = {k: c[k] for k in ('telefone', 'cpf', 'email')}
        tel = None
        if (c['telefone'] or '').strip():
            tel = _tel_canonico(c['telefone'])
            if tel is None:
                # original em digitos: sem DDD nao ha como completar sem palpite
                # (OTP/WhatsApp iriam p/ um estranho) — a equipe corrige a mao
                log('cliente', pk, 'telefone invalido removido',
                    telefone=_mascara('telefone', c['telefone']),
                    telefone_original=_digits(c['telefone']))
        cpf = _digits(c['cpf']) or None
        if cpf and len(cpf) != 11:
            log('cliente', pk, 'cpf invalido removido', cpf=_mascara('cpf', c['cpf']))
            cpf = None
        email = (c['email'] or '').strip() or None
        novos[pk] = {'telefone': tel, 'cpf': cpf, 'email': email}
        score[pk] = (n_atend.get(pk, 0), c['criado_em'], pk)
        if c['deletado_em'] is None:
            ativos.append(pk)

    # ── 2. dedup entre ativos (UNIQUE parciais) — sem soft-delete ──
    for campo in ('telefone', 'cpf', 'email'):
        grupos = {}
        for pk in ativos:
            valor = novos[pk][campo]
            if valor:
                chave = valor.lower() if campo == 'email' else valor
                grupos.setdefault(chave, []).append(pk)
        for pks in grupos.values():
            if len(pks) < 2:
                continue
            pks.sort(key=lambda p: score[p], reverse=True)
            sobrevivente = pks[0]
            for pk in pks[1:]:
                log('cliente', pk, f'{campo} duplicado removido (mantido no cliente {sobrevivente})',
                    duplicada_de=sobrevivente, **{campo: _mascara(campo, novos[pk][campo])})
                novos[pk][campo] = None

    for pk, n in novos.items():
        if n != originais[pk]:
            Cliente.objects.filter(pk=pk).update(**n)

    # ── 3. demais UNIQUE/CHECK novos ──
    # termo: 1 versao ativa por (tipo, procedimento) — fica a mais recente
    vistos = set()
    for t in VersaoTermo.objects.filter(ativa=True).order_by('-vigente_desde', '-pk'):
        chave = (t.tipo, t.procedimento_id)
        if chave in vistos:
            VersaoTermo.objects.filter(pk=t.pk).update(ativa=False)
            log('versao_termo', t.pk, 'versao ativa duplicada desativada')
        vistos.add(chave)
    # preco: 1 por (procedimento, profissional, vigencia) — fica o ultimo cadastrado
    vistos = set()
    for pr in Preco.objects.order_by('-pk').values('pk', 'procedimento_id', 'profissional_id', 'vigente_desde'):
        chave = (pr['procedimento_id'], pr['profissional_id'], pr['vigente_desde'])
        if chave in vistos:
            Preco.objects.filter(pk=pr['pk']).delete()
            log('preco', pr['pk'], 'preco duplicado na mesma vigencia removido')
        vistos.add(chave)
    # lista de espera: 1 inscricao ativa por (cliente, procedimento, data)
    vistos = set()
    for e in ListaEspera.objects.filter(notificado=False).order_by('-pk').values(
        'pk', 'cliente_id', 'procedimento_id', 'data_desejada',
    ):
        chave = (e['cliente_id'], e['procedimento_id'], e['data_desejada'])
        if chave in vistos:
            ListaEspera.objects.filter(pk=e['pk']).update(notificado=True)
        vistos.add(chave)
    # promocao: desconto 0..100, preco >= 0, desconto XOR preco (o painel e o
    # site usam desconto_percentual — ele prevalece)
    # Valor fora da faixa e erro de digitacao: trava no CHECK e DESATIVA (150%
    # virava 100% ativa = servico de graca no site/agendamento); 100% digitado
    # e legitimo e segue ativa. O log guarda o original.
    for p in Promocao.objects.all():
        orig_desc, orig_preco = p.desconto_percentual, p.preco_promocional
        invalida = (orig_desc is not None and not (0 <= orig_desc <= 100)) or (
            orig_preco is not None and orig_preco < 0)
        desconto = min(max(orig_desc or 0, 0), 100)
        preco = orig_preco
        if preco is not None and (preco < 0 or desconto > 0):
            preco = None
        ativa = p.ativa and not invalida
        if desconto != orig_desc or preco != orig_preco or ativa != p.ativa:
            Promocao.objects.filter(pk=p.pk).update(
                desconto_percentual=desconto, preco_promocional=preco, ativa=ativa)
            log('promocao', p.pk,
                'desconto/preco promocional ajustado ao CHECK'
                + (' (valor invalido: promocao desativada)' if invalida and p.ativa else ''),
                desconto_de=None if orig_desc is None else str(orig_desc),
                preco_de=None if orig_preco is None else str(orig_preco),
                desativada=bool(invalida and p.ativa))

    LogAuditoria.objects.bulk_create(logs)

    if schema_editor.connection.vendor == 'postgresql':
        # FKs sao DEFERRABLE INITIALLY DEFERRED: dispara os checks agora p/ o
        # CREATE INDEX / ALTER TABLE seguintes nao verem trigger events pendentes
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


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
        # unicidades antigas saem ANTES da normalizacao (evita colisao no meio)
        migrations.AlterField(
            model_name='cliente',
            name='cpf',
            field=models.CharField(blank=True, max_length=14, null=True, validators=[aranha_estetica.validators.validate_cpf]),
        ),
        migrations.RemoveConstraint(
            model_name='cliente',
            name='uniq_cliente_email_ativo',
        ),
        migrations.RunPython(normalizar_e_deduplicar, migrations.RunPython.noop),
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
