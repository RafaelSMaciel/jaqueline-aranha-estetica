# Padrao SQL — fase (c): consentimentos com historico (docs/specs/padrao-sql.md §2.7, §4.c, §6)
#
#   cliente.consent_email_marketing[_em|_ip]       -> consentimento MARKETING / EMAIL
#   cliente.consent_whatsapp_nps[_em|_ip]          -> consentimento PESQUISA_SATISFACAO / WHATSAPP
#   cliente.consent_whatsapp_confirmacao[_em|_ip]  -> consentimento LEMBRETE_AGENDAMENTO / WHATSAPP
#   cliente.aceita_comunicacao = false             -> revogacao de MARKETING/EMAIL e
#                                                     PESQUISA_SATISFACAO/WHATSAPP (opt-out geral)
#
# Por cliente (inclusive excluido/anonimizado) e por par, origem MIGRACAO:
#   consent_x = true                 -> linha concedida em consent_x_em (ou cliente.criado_em), IP = consent_x_ip
#   consent_x = false com consent_x_em -> linha revogada nessa data/IP (o wizard gravava a revogacao;
#                                       descadastro/admin antigos nao atualizavam a data: ela pode ser a
#                                       da concessao — o estado e o mesmo)
#   consent_x = false sem data       -> nada (nunca concedeu = sem linha)
#   aceita_comunicacao = false       -> revogacao dos 2 pares em greatest(atualizado_em, criado_em,
#                                       datas acima) + 1 microssegundo: fica por ultimo e preserva o
#                                       efeito de hoje (vetar). aceita_comunicacao = true nao vira nada.
# Invariante (teste em test_migrations_dados): estado vigente de cada par =
#   consent_x AND (aceita_comunicacao OR par = LEMBRETE_AGENDAMENTO).
#
# Ordem: CreateModel -> RunPython (historico + LogAuditoria; termina com
# SET CONSTRAINTS ALL IMMEDIATE) -> PG: fn_consentimento_imutavel + tg_consentimento_imutavel
# -> PadronizarNomes -> RemoveField x10.
#
# Reverso (dev/testes; rollback de producao = restaurar o dump): recria as colunas a partir da
# linha vigente de cada par (consent_x/_em/_ip) e aceita_comunicacao = false quando a revogacao
# mais recente de opt-out geral (MIGRACAO 'opt-out geral', DESCADASTRO ou ESQUECIMENTO) de
# MARKETING/PESQUISA e posterior a qualquer concessao desses pares.

from collections import defaultdict
from datetime import timedelta

import django.db.models.deletion
import django.db.models.functions.datetime
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import aranha_estetica.db.indices
from aranha_estetica.db.operacoes import PadronizarNomes

APP = 'aranha_estetica'
PARES = (
    ('email_marketing', 'MARKETING', 'EMAIL'),
    ('whatsapp_nps', 'PESQUISA_SATISFACAO', 'WHATSAPP'),
    ('whatsapp_confirmacao', 'LEMBRETE_AGENDAMENTO', 'WHATSAPP'),
)
# pares que o antigo aceita_comunicacao vetava (o lembrete e transacional)
PARES_OPT_OUT = (('MARKETING', 'EMAIL'), ('PESQUISA_SATISFACAO', 'WHATSAPP'))
TEXTO_OPT_OUT = 'opt-out geral (aceita_comunicacao=false)'
UM_MICROSSEGUNDO = timedelta(microseconds=1)

SQL_FUNCAO = """
CREATE OR REPLACE FUNCTION fn_consentimento_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'consentimento e historico append-only: exclusao bloqueada'
            USING DETAIL = 'consentimento.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- unica mutacao aceita: minimizacao LGPD (anonimizacao apaga IP e user-agent)
    IF (NEW.id, NEW.cliente_id, NEW.finalidade, NEW.canal, NEW.eh_concedido, NEW.origem, NEW.texto,
        NEW.usuario_autor_id, NEW.registrado_em, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.cliente_id, OLD.finalidade, OLD.canal, OLD.eh_concedido, OLD.origem, OLD.texto,
        OLD.usuario_autor_id, OLD.registrado_em, OLD.criado_em)
       OR (NEW.ip_origem IS DISTINCT FROM OLD.ip_origem AND NEW.ip_origem IS NOT NULL)
       OR (NEW.user_agent IS DISTINCT FROM OLD.user_agent AND NEW.user_agent <> '') THEN
        RAISE EXCEPTION 'consentimento e historico append-only: registro imutavel'
            USING DETAIL = 'consentimento.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""

SQL_PG = [
    SQL_FUNCAO,
    "CREATE TRIGGER tg_consentimento_imutavel BEFORE UPDATE OR DELETE ON consentimento "
    "FOR EACH ROW EXECUTE FUNCTION fn_consentimento_imutavel()",
]
SQL_PG_REVERSO = [
    'DROP TRIGGER IF EXISTS tg_consentimento_imutavel ON consentimento',
    'DROP FUNCTION IF EXISTS fn_consentimento_imutavel()',
]


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def migrar_consentimentos(apps, schema_editor):
    Cliente = apps.get_model(APP, 'Cliente')
    Consentimento = apps.get_model(APP, 'Consentimento')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    campos = ['pk', 'criado_em', 'atualizado_em', 'aceita_comunicacao']
    for sufixo, _f, _c in PARES:
        campos += [f'consent_{sufixo}', f'consent_{sufixo}_em', f'consent_{sufixo}_ip']

    novos = []
    contagem = defaultdict(int)
    for c in Cliente._base_manager.order_by('pk').values(*campos):
        datas = [d for d in (c['criado_em'], c['atualizado_em']) if d is not None]
        for sufixo, finalidade, canal in PARES:
            concedido = c[f'consent_{sufixo}']
            em, ip = c[f'consent_{sufixo}_em'], c[f'consent_{sufixo}_ip']
            if em is not None:
                datas.append(em)
            if concedido:
                quando, texto = em or c['criado_em'], f'(migrado de cliente.consent_{sufixo})'
            elif em is not None:
                quando, texto = em, f'(migrado de cliente.consent_{sufixo}=false)'
            else:
                continue
            novos.append(Consentimento(
                cliente_id=c['pk'], finalidade=finalidade, canal=canal, concedido=bool(concedido),
                origem='MIGRACAO', texto=texto, ip_origem=ip, registrado_em=quando,
            ))
            contagem[f'{finalidade}:{canal}:{"concedido" if concedido else "revogado"}'] += 1
        if not c['aceita_comunicacao']:
            quando = max(datas) + UM_MICROSSEGUNDO if datas else django.utils.timezone.now()
            for finalidade, canal in PARES_OPT_OUT:
                novos.append(Consentimento(
                    cliente_id=c['pk'], finalidade=finalidade, canal=canal, concedido=False,
                    origem='MIGRACAO', texto=TEXTO_OPT_OUT, registrado_em=quando,
                ))
            contagem['opt_out_geral'] += 1

    Consentimento.objects.bulk_create(novos, batch_size=500)
    if novos:
        LogAuditoria.objects.create(
            acao='migration 0050: consentimentos com historico (consentimento)',
            tabela='consentimento',
            detalhes={'linhas': len(novos), **dict(contagem)},
        )
    if _pg(schema_editor):
        # FKs DEFERRABLE: dispara os checks agora (o ALTER TABLE seguinte nao pode
        # ver trigger events pendentes — padrao 0034-0049)
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def _eh_opt_out_geral(linha):
    return not linha.concedido and (
        linha.origem in ('DESCADASTRO', 'ESQUECIMENTO')
        or (linha.origem == 'MIGRACAO' and linha.texto == TEXTO_OPT_OUT)
    )


def restaurar_colunas(apps, schema_editor):
    """Reverso (dev/testes): colunas antigas a partir do estado vigente de cada par."""
    Cliente = apps.get_model(APP, 'Cliente')
    Consentimento = apps.get_model(APP, 'Consentimento')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    por_cliente = defaultdict(list)
    for linha in Consentimento.objects.order_by('cliente_id', '-registrado_em', '-id'):
        por_cliente[linha.cliente_id].append(linha)
    clientes = list(Cliente._base_manager.filter(pk__in=list(por_cliente)))
    for cliente in clientes:
        linhas = por_cliente[cliente.pk]
        vigente = {}
        for linha in linhas:
            vigente.setdefault((linha.finalidade, linha.canal), linha)
        for sufixo, finalidade, canal in PARES:
            linha = vigente.get((finalidade, canal))
            setattr(cliente, f'consent_{sufixo}', bool(linha and linha.concedido))
            setattr(cliente, f'consent_{sufixo}_em', linha.registrado_em if linha else None)
            setattr(cliente, f'consent_{sufixo}_ip', linha.ip_origem if linha else None)
        do_opt_out = [x for x in linhas if (x.finalidade, x.canal) in PARES_OPT_OUT]
        ultimo_opt_out = next((x.registrado_em for x in do_opt_out if _eh_opt_out_geral(x)), None)
        ultima_concessao = next((x.registrado_em for x in do_opt_out if x.concedido), None)
        cliente.aceita_comunicacao = not (
            ultimo_opt_out is not None and (ultima_concessao is None or ultimo_opt_out > ultima_concessao)
        )
    campos = ['aceita_comunicacao'] + [
        f'consent_{s}{x}' for s, _f, _c in PARES for x in ('', '_em', '_ip')
    ]
    Cliente._base_manager.bulk_update(clientes, campos, batch_size=500)
    if clientes:
        LogAuditoria.objects.create(
            acao='migration 0050 (reversa): consentimentos voltaram para colunas do cliente',
            tabela='cliente', detalhes={'clientes': len(clientes)},
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
        ('aranha_estetica', '0049_link_acesso'),
    ]

    operations = [
        migrations.CreateModel(
            name='Consentimento',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('finalidade', models.CharField(choices=[('MARKETING', 'Promoções e novidades'), ('PESQUISA_SATISFACAO', 'Pesquisa de satisfação'), ('LEMBRETE_AGENDAMENTO', 'Lembrete do agendamento')], max_length=30)),
                ('canal', models.CharField(choices=[('EMAIL', 'E-mail'), ('WHATSAPP', 'WhatsApp'), ('SMS', 'SMS')], max_length=20)),
                ('concedido', models.BooleanField(db_column='eh_concedido')),
                ('origem', models.CharField(choices=[('AGENDAMENTO_ONLINE', 'Agendamento online (a própria cliente)'), ('DESCADASTRO', 'Link de descadastro'), ('PAINEL', 'Painel (equipe)'), ('ESQUECIMENTO', 'Direito ao esquecimento'), ('MIGRACAO', 'Migração do cadastro antigo'), ('ADMIN', 'Django admin')], max_length=30)),
                ('texto', models.TextField(blank=True, db_comment='Texto exibido ao titular no momento (prova do que foi consentido).', db_default='', default='')),
                ('ip_origem', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, db_default='', default='', max_length=500)),
                ('registrado_em', models.DateTimeField(db_comment='Quando o titular concedeu/revogou (na migracao = data original de consent_*_em).', db_default=django.db.models.functions.datetime.Now(), default=django.utils.timezone.now)),
                ('criado_em', models.DateTimeField(db_default=django.db.models.functions.datetime.Now(), default=django.utils.timezone.now, editable=False)),
                ('cliente', models.ForeignKey(db_index=False, on_delete=django.db.models.deletion.PROTECT, related_name='consentimentos', to='aranha_estetica.cliente')),
                ('usuario_autor', models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'consentimento',
                'db_table_comment': 'Historico append-only de concessao/revogacao por finalidade x canal (LGPD art. 8). Estado atual = linha mais recente (vw_consentimento_atual / fn_tem_consentimento). Sem linha = nao concedido.',
                'indexes': [
                    aranha_estetica.db.indices.Indice(fields=['cliente', 'finalidade', 'canal', '-registrado_em', '-id'], name='ix_consentimento_cliente_finalidade_canal_registrado_em'),
                    aranha_estetica.db.indices.Indice(condition=models.Q(('usuario_autor__isnull', False)), fields=['usuario_autor'], name='ix_consentimento_usuario_autor_parcial'),
                ],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('finalidade__in', ['MARKETING', 'PESQUISA_SATISFACAO', 'LEMBRETE_AGENDAMENTO'])), name='ck_consentimento_finalidade'),
                    models.CheckConstraint(condition=models.Q(('canal__in', ['EMAIL', 'WHATSAPP', 'SMS'])), name='ck_consentimento_canal'),
                    models.CheckConstraint(condition=models.Q(models.Q(('canal', 'EMAIL'), ('finalidade', 'MARKETING')), models.Q(('canal', 'WHATSAPP'), ('finalidade', 'PESQUISA_SATISFACAO')), models.Q(('canal', 'WHATSAPP'), ('finalidade', 'LEMBRETE_AGENDAMENTO')), _connector='OR'), name='ck_consentimento_finalidade_canal'),
                    models.CheckConstraint(condition=models.Q(('origem__in', ['AGENDAMENTO_ONLINE', 'DESCADASTRO', 'PAINEL', 'ESQUECIMENTO', 'MIGRACAO', 'ADMIN'])), name='ck_consentimento_origem'),
                ],
            },
        ),
        migrations.RunPython(migrar_consentimentos, restaurar_colunas),
        migrations.RunPython(aplicar_objetos_pg, remover_objetos_pg),
        PadronizarNomes(tabelas=['consentimento']),
        migrations.RemoveField(model_name='cliente', name='aceita_comunicacao'),
        migrations.RemoveField(model_name='cliente', name='consent_email_marketing'),
        migrations.RemoveField(model_name='cliente', name='consent_email_marketing_em'),
        migrations.RemoveField(model_name='cliente', name='consent_email_marketing_ip'),
        migrations.RemoveField(model_name='cliente', name='consent_whatsapp_confirmacao'),
        migrations.RemoveField(model_name='cliente', name='consent_whatsapp_confirmacao_em'),
        migrations.RemoveField(model_name='cliente', name='consent_whatsapp_confirmacao_ip'),
        migrations.RemoveField(model_name='cliente', name='consent_whatsapp_nps'),
        migrations.RemoveField(model_name='cliente', name='consent_whatsapp_nps_em'),
        migrations.RemoveField(model_name='cliente', name='consent_whatsapp_nps_ip'),
    ]
