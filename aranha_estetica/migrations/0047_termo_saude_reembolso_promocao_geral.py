# Auditoria pre-producao (rodada final, dados/consentimento):
#  - VersaoTermo.tipo ganha 'SAUDE': o consentimento especifico p/ dado de
#    saude (LGPD art. 11, I) vira AceiteTermo com prova (IP, user-agent,
#    SHA-256 do texto) no booking e na ficha publica — antes so havia o texto
#    no LogAuditoria. CHECK chk_versao_termo_tipo refeito; SAUDE v1.0 ativa
#    criada (texto = models.termos.TEXTO_CONSENTIMENTO_SAUDE) se nao houver;
#    LogAuditoria da publicacao so em banco que ja opera (como a 0045).
#    Triggers de imutabilidade da 0046 (aceite_termo / versao_termo) nao sao
#    tocados: DROP/ADD CONSTRAINT nao recria a tabela no Postgres.
#  - CompraPacote.valor_reembolsado (default 0; CHECK 0 <= reembolso <= pago):
#    o cancelamento grava o valor devolvido e a receita usa o liquido.
#  - Promocao geral (procedimento NULL) so por percentual: preco fixo geral e
#    ignorado no preco (utils.precos) e viraria teto do catalogo. Legado com
#    preco fixo geral: preco removido e promocao DESATIVADA, com LogAuditoria
#    (o XOR da 0034 ja zerou o desconto dessas — ficariam sem efeito).
# Reverso: rollback de deploy = restaurar o dump (migrate_atomico). Em dev, a
# reversa apaga o termo SAUDE sem aceites e recusa se ja houver aceite (prova).

import django.core.validators
from django.db import migrations, models


def sanear_promocao_geral_preco_fixo(apps, schema_editor):
    Promocao = apps.get_model('aranha_estetica', 'Promocao')
    LogAuditoria = apps.get_model('aranha_estetica', 'LogAuditoria')
    logs = []
    for p in Promocao.objects.filter(procedimento__isnull=True, preco_promocional__isnull=False):
        Promocao.objects.filter(pk=p.pk).update(preco_promocional=None, ativa=False)
        logs.append(LogAuditoria(
            acao='migration 0047: promocao geral com preco fixo (sem efeito no preco) desativada',
            tabela='promocao',
            registro_id=p.pk,
            detalhes={'preco_de': str(p.preco_promocional), 'desativada': p.ativa},
        ))
    LogAuditoria.objects.bulk_create(logs)
    if schema_editor.connection.vendor == 'postgresql':
        # FKs DEFERRABLE: dispara os checks agora p/ o ALTER TABLE seguinte
        # nao ver trigger events pendentes (mesmo padrao da 0034)
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def criar_termo_saude_v1(apps, schema_editor):
    from django.utils import timezone

    from aranha_estetica.models.termos import (
        TERMO_SAUDE_TITULO, TERMO_SAUDE_VERSAO, TEXTO_CONSENTIMENTO_SAUDE,
    )

    VersaoTermo = apps.get_model('aranha_estetica', 'VersaoTermo')
    Cliente = apps.get_model('aranha_estetica', 'Cliente')
    LogAuditoria = apps.get_model('aranha_estetica', 'LogAuditoria')
    if VersaoTermo.objects.filter(tipo='SAUDE', procedimento__isnull=True, ativa=True).exists():
        return
    termo = VersaoTermo.objects.create(
        tipo='SAUDE', procedimento=None, versao=TERMO_SAUDE_VERSAO,
        titulo=TERMO_SAUDE_TITULO, conteudo=TEXTO_CONSENTIMENTO_SAUDE,
        vigente_desde=timezone.localdate(), ativa=True,
    )
    if not Cliente.objects.exists():
        return  # instalacao limpa: nada operando p/ a trilha explicar
    LogAuditoria.objects.create(
        acao=f'migration 0047: termo SAUDE v{TERMO_SAUDE_VERSAO} publicado '
             '(consentimento de dados de saude, LGPD art. 11)',
        tabela='versao_termo',
        registro_id=termo.pk,
    )


def remover_termo_saude(apps, schema_editor):
    """Reversa (dev): o CHECK antigo nao aceita SAUDE. Aceite e prova legal:
    com aceite gravado a reversa e recusada (restaure o dump)."""
    AceiteTermo = apps.get_model('aranha_estetica', 'AceiteTermo')
    if AceiteTermo.objects.filter(versao_termo__tipo='SAUDE').exists():
        raise RuntimeError(
            'migration 0047 (reversa): ha aceites de termo SAUDE (prova LGPD art. 11) — '
            'restaure o dump em vez de reverter.'
        )
    # SQL direto: o Collector do delete() com models historicos quebra na FK
    # RESTRICT de aceite_termo (sem aceite, nada a proteger)
    schema_editor.execute('DELETE FROM versao_termo WHERE tipo = %s', ['SAUDE'])
    if schema_editor.connection.vendor == 'postgresql':
        # o DELETE deixa o check (deferido) da FK de aceite_termo pendente: o
        # ALTER TABLE versao_termo seguinte da reversa falharia
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0046_prontuario_versao_autoria_log_prova_aceite'),
    ]

    operations = [
        # 1. termo SAUDE (choices + CHECK)
        migrations.RemoveConstraint(
            model_name='versaotermo',
            name='chk_versao_termo_tipo',
        ),
        migrations.AlterField(
            model_name='versaotermo',
            name='tipo',
            field=models.CharField(choices=[('LGPD', 'LGPD / Privacidade'), ('SAUDE', 'Dados de saúde (LGPD art. 11)'), ('PROCEDIMENTO', 'Termo de Procedimento')], max_length=20),
        ),
        migrations.AddConstraint(
            model_name='versaotermo',
            constraint=models.CheckConstraint(condition=models.Q(('tipo__in', ['LGPD', 'SAUDE', 'PROCEDIMENTO'])), name='chk_versao_termo_tipo'),
        ),
        # 2. reembolso do pacote
        migrations.AddField(
            model_name='comprapacote',
            name='valor_reembolsado',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddConstraint(
            model_name='comprapacote',
            constraint=models.CheckConstraint(condition=models.Q(('valor_reembolsado__gte', 0), ('valor_reembolsado__lte', models.F('valor_pago'))), name='chk_compra_pacote_valor_reembolsado'),
        ),
        # 3. promocao geral so percentual (saneia antes do CHECK)
        migrations.RunPython(sanear_promocao_geral_preco_fixo, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='promocao',
            constraint=models.CheckConstraint(condition=models.Q(('procedimento__isnull', False), ('preco_promocional__isnull', True), _connector='OR'), name='chk_promocao_geral_so_percentual'),
        ),
        # 4. dado: SAUDE v1.0 ativa (por ultimo: nenhum DDL depois do INSERT)
        migrations.RunPython(criar_termo_saude_v1, remover_termo_saude),
    ]
