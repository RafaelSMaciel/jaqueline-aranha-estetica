# Padrao SQL — fase (b): tokens em hash (docs/specs/padrao-sql.md §2.7, §4.b, §6)
#
#   atendimento.token_cancelamento    -> link_acesso GERENCIAR_AGENDAMENTO (expira no inicio)
#   notificacao.token                 -> CONFIRMAR_PRESENCA | NPS | TERMO (pela rota que o aceitava)
#   resposta_anamnese.token           -> FORMULARIO (criado_em + 60 dias)
#   cliente.token_descadastro         -> DESCADASTRO (sem expiracao)
#   profissional.ics_token            -> FEED_ICS (sem expiracao)
#   lista_espera.token_reserva/expira_em: legado sem uso -> so removidos
#
# Os links JA ENVIADOS continuam validos: token_hash = sha256(token atual), sem
# chave (rotacionar a SECRET_KEY nao derruba link enviado — spec §7.4).
#
# Ordem: CreateModel (CHECK/UNIQUE/indices portaveis) -> AlterField
# resposta_anamnese.token NULL (so p/ o reverso conseguir recriar a coluna) ->
# RunPython (sha256 dos tokens + LogAuditoria) -> SET CONSTRAINTS ALL IMMEDIATE
# -> PG: ck_link_acesso_token_hash_formato, fn_link_acesso_imutavel +
# tg_link_acesso_imutavel -> PadronizarNomes -> RemoveField x7 (o DROP COLUMN
# leva junto os UNIQUE e os _like, inclusive os de nome legado).
#
# Notificacao -> finalidade = a rota que aceitava o token antes (nada passa a
# abrir onde antes dava 404): NPS (/nps/, qualquer canal); TERMO ou LEMBRETE por
# EMAIL (formato legado do termo) -> TERMO; LEMBRETE/LEMBRETE_2H/CONFIRMACAO por
# WHATSAPP -> CONFIRMAR_PRESENCA. O resto (CANCELAMENTO, PESQUISA, APROVACAO,
# lembrete por SMS) nao tinha rota: nao migra (ids no LogAuditoria).
# Descadastro: so cliente nao excluido (o resolver antigo ignorava soft-delete).
# Token repetido entre tabelas (dado sujo; UNIQUE so por tabela): fica o primeiro
# na ordem acima, os outros vao p/ o log.
#
# Reverso (dev/testes; rollback de producao = restaurar o dump): recria as
# colunas e grava TOKENS NOVOS (hash nao volta a texto) — links antigos deixam
# de funcionar.

import hashlib
import secrets
from collections import defaultdict
from datetime import timedelta

import django.db.models.deletion
import django.db.models.functions.datetime
import django.utils.timezone
from django.db import migrations, models

import aranha_estetica.db.campos
import aranha_estetica.db.indices
from aranha_estetica.db.operacoes import PadronizarNomes

APP = 'aranha_estetica'
NPS_VALIDADE = timedelta(days=7)          # views.admin_management.NPS_TOKEN_EXPIRY @ 0048
FORMULARIO_VALIDADE = timedelta(days=60)  # views.anamnese_publica._get_resposta_or_404 @ 0048

SQL_FUNCAO = """
CREATE OR REPLACE FUNCTION fn_link_acesso_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- DELETE livre (purga/cascata). UPDATE: so marcar usado_em e revogado_em, uma vez cada.
    IF (NEW.id, NEW.finalidade, NEW.atendimento_id, NEW.notificacao_id, NEW.resposta_formulario_id,
        NEW.cliente_id, NEW.profissional_id, NEW.token_hash, NEW.expira_em, NEW.criado_em)
       IS DISTINCT FROM
       (OLD.id, OLD.finalidade, OLD.atendimento_id, OLD.notificacao_id, OLD.resposta_formulario_id,
        OLD.cliente_id, OLD.profissional_id, OLD.token_hash, OLD.expira_em, OLD.criado_em)
       OR (OLD.usado_em IS NOT NULL AND NEW.usado_em IS DISTINCT FROM OLD.usado_em)
       OR (OLD.revogado_em IS NOT NULL AND NEW.revogado_em IS DISTINCT FROM OLD.revogado_em) THEN
        RAISE EXCEPTION 'link_acesso: so usado_em/revogado_em podem ser preenchidos (uma vez)'
            USING DETAIL = 'link_acesso.id=' || OLD.id, ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""

SQL_PG = [
    "ALTER TABLE link_acesso ADD CONSTRAINT ck_link_acesso_token_hash_formato "
    "CHECK (token_hash ~ '^[0-9a-f]{64}$')",
    SQL_FUNCAO,
    "CREATE TRIGGER tg_link_acesso_imutavel BEFORE UPDATE ON link_acesso "
    "FOR EACH ROW EXECUTE FUNCTION fn_link_acesso_imutavel()",
]
SQL_PG_REVERSO = [
    'DROP TRIGGER IF EXISTS tg_link_acesso_imutavel ON link_acesso',
    'DROP FUNCTION IF EXISTS fn_link_acesso_imutavel()',
    'ALTER TABLE link_acesso DROP CONSTRAINT IF EXISTS ck_link_acesso_token_hash_formato',
]


def _pg(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def _sha256(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def finalidade_notificacao(tipo, canal):
    """Rota que aceitava o token da notificacao antes da 0049 (None = nenhuma)."""
    if tipo == 'NPS':
        return 'NPS'
    if tipo == 'TERMO' or (tipo == 'LEMBRETE' and canal == 'EMAIL'):
        return 'TERMO'
    if tipo in ('LEMBRETE', 'LEMBRETE_2H', 'CONFIRMACAO') and canal == 'WHATSAPP':
        return 'CONFIRMAR_PRESENCA'
    return None


def _com_token(qs, campo):
    return qs.exclude(**{f'{campo}__isnull': True}).exclude(**{campo: ''})


def migrar_tokens(apps, schema_editor):
    LinkAcesso = apps.get_model(APP, 'LinkAcesso')
    Atendimento = apps.get_model(APP, 'Atendimento')
    Notificacao = apps.get_model(APP, 'Notificacao')
    RespostaAnamnese = apps.get_model(APP, 'RespostaAnamnese')
    Cliente = apps.get_model(APP, 'Cliente')
    Profissional = apps.get_model(APP, 'Profissional')
    AvaliacaoNPS = apps.get_model(APP, 'AvaliacaoNPS')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    novos, vistos = [], {}
    contagem = defaultdict(int)
    avisos = defaultdict(list)

    def link(finalidade, origem, token, **campos):
        token_hash = _sha256(token)
        if token_hash in vistos:
            avisos['token_repetido_descartado'].append(f'{origem} (ja usado por {vistos[token_hash]})')
            return
        vistos[token_hash] = origem
        contagem[finalidade] += 1
        novos.append(LinkAcesso(finalidade=finalidade, token_hash=token_hash, **campos))

    # 1. reagendar/cancelar (links /reagendar/<token>/ e o POST de cancelamento)
    for pk, token, inicio in _com_token(Atendimento.objects, 'token_cancelamento').order_by('pk').values_list(
        'pk', 'token_cancelamento', 'data_hora_inicio',
    ):
        link('GERENCIAR_AGENDAMENTO', f'atendimento {pk}', token, atendimento_id=pk, expira_em=inicio)

    # 2. notificacoes (/confirmar/, /nps/, /termo/)
    avaliado_em = dict(AvaliacaoNPS.objects.values_list('atendimento_id', 'criado_em'))
    for n in _com_token(Notificacao.objects, 'token').select_related('atendimento').order_by('pk'):
        finalidade = finalidade_notificacao(n.tipo, n.canal)
        if finalidade is None:
            avisos['notificacao_sem_rota_nao_migrada'].append(n.pk)
            continue
        if finalidade == 'NPS':
            expira = n.criado_em + NPS_VALIDADE
            usado = n.respondido_em or avaliado_em.get(n.atendimento_id)
        elif finalidade == 'TERMO':
            expira, usado = n.atendimento.data_hora_fim, n.respondido_em
        else:
            expira, usado = n.atendimento.data_hora_inicio, n.respondido_em
        link(finalidade, f'notificacao {n.pk}', n.token, notificacao_id=n.pk, expira_em=expira, usado_em=usado)

    # 3. fichas/pesquisas (/anamnese/<token>/, /pesquisa/<token>/)
    for pk, token, criado, respondida in _com_token(RespostaAnamnese.objects, 'token').order_by('pk').values_list(
        'pk', 'token', 'criado_em', 'respondida_em',
    ):
        link('FORMULARIO', f'resposta_anamnese {pk}', token, resposta_formulario_id=pk,
             expira_em=criado + FORMULARIO_VALIDADE, usado_em=respondida)

    # 4. descadastro (rodape + List-Unsubscribe dos e-mails de marketing)
    for pk, token in _com_token(Cliente.objects.filter(deletado_em__isnull=True), 'token_descadastro') \
            .order_by('pk').values_list('pk', 'token_descadastro'):
        link('DESCADASTRO', f'cliente {pk}', token, cliente_id=pk)

    # 5. feed ICS do profissional
    for pk, token in _com_token(Profissional.objects, 'ics_token').order_by('pk').values_list('pk', 'ics_token'):
        link('FEED_ICS', f'profissional {pk}', token, profissional_id=pk)

    LinkAcesso.objects.bulk_create(novos, batch_size=500)

    if novos or avisos:
        LogAuditoria.objects.create(
            acao='migration 0049: links publicos em hash (link_acesso)',
            tabela='link_acesso',
            detalhes={'migrados': dict(contagem), **{k: v for k, v in avisos.items()}},
        )
    if _pg(schema_editor):
        # FKs DEFERRABLE: dispara os checks agora (o ALTER TABLE seguinte nao pode
        # ver trigger events pendentes — padrao 0034-0048)
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE', None)


def restaurar_tokens(apps, schema_editor):
    """Reverso (dev/testes): colunas antigas com TOKENS NOVOS (os links antigos param)."""
    Atendimento = apps.get_model(APP, 'Atendimento')
    Notificacao = apps.get_model(APP, 'Notificacao')
    RespostaAnamnese = apps.get_model(APP, 'RespostaAnamnese')
    Cliente = apps.get_model(APP, 'Cliente')
    Profissional = apps.get_model(APP, 'Profissional')
    LinkAcesso = apps.get_model(APP, 'LinkAcesso')
    LogAuditoria = apps.get_model(APP, 'LogAuditoria')

    def preencher(Model, campo, qs=None):
        objs = list((qs if qs is not None else Model.objects.all()).only('pk'))
        for obj in objs:
            setattr(obj, campo, secrets.token_urlsafe(32))
        Model.objects.bulk_update(objs, [campo], batch_size=500)
        return len(objs)

    # o modelo antigo gerava token no save(): todos tinham
    qtd = preencher(Atendimento, 'token_cancelamento')
    qtd += preencher(Cliente, 'token_descadastro')
    qtd += preencher(Profissional, 'ics_token')
    qtd += preencher(RespostaAnamnese, 'token')
    # notificacao so tinha token quando carregava link
    qtd += preencher(Notificacao, 'token', Notificacao.objects.filter(
        pk__in=LinkAcesso.objects.filter(notificacao__isnull=False).values('notificacao_id'),
    ))
    if LinkAcesso.objects.exists():
        LogAuditoria.objects.create(
            acao='migration 0049 (reversa): tokens em texto puro regerados (links anteriores invalidos)',
            tabela='link_acesso', detalhes={'tokens_novos': qtd},
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
        ('aranha_estetica', '0048_agenda_unificada'),
    ]

    operations = [
        migrations.CreateModel(
            name='LinkAcesso',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('finalidade', models.CharField(choices=[('GERENCIAR_AGENDAMENTO', 'Reagendar/cancelar (link legado)'), ('CONFIRMAR_PRESENCA', 'Confirmar presença'), ('NPS', 'Pesquisa NPS'), ('TERMO', 'Aceite de termo'), ('FORMULARIO', 'Ficha/pesquisa'), ('DESCADASTRO', 'Descadastro de comunicação'), ('FEED_ICS', 'Feed do calendário (ICS)')], max_length=30)),
                ('token_hash', aranha_estetica.db.campos.HashSha256Field(db_comment='sha256(token) hex. Sem pepper/HMAC de proposito: rotacionar a SECRET_KEY (incidente, PROJECT §9.5) nao pode invalidar links ja enviados.', editable=False)),
                ('expira_em', models.DateTimeField(blank=True, null=True)),
                ('usado_em', models.DateTimeField(blank=True, db_comment='Primeiro uso efetivo (acao concluida). Links de uso multiplo (ICS, descadastro, gerenciar) continuam validos.', null=True)),
                ('revogado_em', models.DateTimeField(blank=True, db_comment='Revogado (rotacao do ICS, anonimizacao). Link revogado = 404.', null=True)),
                ('criado_em', models.DateTimeField(db_default=django.db.models.functions.datetime.Now(), default=django.utils.timezone.now, editable=False)),
                ('atendimento', models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='links_acesso', to='aranha_estetica.atendimento')),
                ('cliente', models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='links_acesso', to='aranha_estetica.cliente')),
                ('notificacao', models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='links_acesso', to='aranha_estetica.notificacao')),
                ('profissional', models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='links_acesso', to='aranha_estetica.profissional')),
                ('resposta_formulario', models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='links_acesso', to='aranha_estetica.respostaanamnese')),
            ],
            options={
                'db_table': 'link_acesso',
                'db_table_comment': 'Link publico com token (link magico). So o SHA-256 do token e gravado: o token em claro existe apenas na URL entregue. Alvo tipado por finalidade (exatamente 1 FK preenchida).',
                'indexes': [
                    aranha_estetica.db.indices.Indice(condition=models.Q(('atendimento__isnull', False)), fields=['atendimento'], name='ix_link_acesso_atendimento_parcial'),
                    aranha_estetica.db.indices.Indice(condition=models.Q(('notificacao__isnull', False)), fields=['notificacao'], name='ix_link_acesso_notificacao_parcial'),
                    aranha_estetica.db.indices.Indice(condition=models.Q(('resposta_formulario__isnull', False)), fields=['resposta_formulario'], name='ix_link_acesso_resposta_formulario_parcial'),
                    aranha_estetica.db.indices.Indice(condition=models.Q(('cliente__isnull', False)), fields=['cliente'], name='ix_link_acesso_cliente_parcial'),
                    aranha_estetica.db.indices.Indice(condition=models.Q(('profissional__isnull', False)), fields=['profissional'], name='ix_link_acesso_profissional_parcial'),
                    aranha_estetica.db.indices.Indice(condition=models.Q(('expira_em__isnull', False)), fields=['expira_em'], name='ix_link_acesso_expira_em_parcial'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('token_hash',), name='uq_link_acesso_token_hash'),
                    models.CheckConstraint(condition=models.Q(('finalidade__in', ['GERENCIAR_AGENDAMENTO', 'CONFIRMAR_PRESENCA', 'NPS', 'TERMO', 'FORMULARIO', 'DESCADASTRO', 'FEED_ICS'])), name='ck_link_acesso_finalidade'),
                    models.CheckConstraint(condition=models.Q(models.Q(('finalidade', 'GERENCIAR_AGENDAMENTO'), ('atendimento__isnull', False), ('cliente__isnull', True), ('notificacao__isnull', True), ('profissional__isnull', True), ('resposta_formulario__isnull', True)), models.Q(('finalidade__in', ['CONFIRMAR_PRESENCA', 'NPS', 'TERMO']), ('atendimento__isnull', True), ('cliente__isnull', True), ('notificacao__isnull', False), ('profissional__isnull', True), ('resposta_formulario__isnull', True)), models.Q(('finalidade', 'FORMULARIO'), ('atendimento__isnull', True), ('cliente__isnull', True), ('notificacao__isnull', True), ('profissional__isnull', True), ('resposta_formulario__isnull', False)), models.Q(('finalidade', 'DESCADASTRO'), ('atendimento__isnull', True), ('cliente__isnull', False), ('notificacao__isnull', True), ('profissional__isnull', True), ('resposta_formulario__isnull', True)), models.Q(('finalidade', 'FEED_ICS'), ('atendimento__isnull', True), ('cliente__isnull', True), ('notificacao__isnull', True), ('profissional__isnull', False), ('resposta_formulario__isnull', True)), _connector='OR'), name='ck_link_acesso_alvo_por_finalidade'),
                ],
            },
        ),
        # So p/ o reverso: recriar uma coluna NOT NULL UNIQUE com default callable
        # daria o MESMO token a todas as linhas. Nula, o reverso preenche um por linha
        # e o AlterField reverso volta ao NOT NULL.
        migrations.AlterField(
            model_name='respostaanamnese',
            name='token',
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True, unique=True),
        ),
        migrations.RunPython(migrar_tokens, restaurar_tokens),
        migrations.RunPython(aplicar_objetos_pg, remover_objetos_pg),
        PadronizarNomes(tabelas=['link_acesso']),
        migrations.RemoveField(model_name='atendimento', name='token_cancelamento'),
        migrations.RemoveField(model_name='notificacao', name='token'),
        migrations.RemoveField(model_name='respostaanamnese', name='token'),
        migrations.RemoveField(model_name='cliente', name='token_descadastro'),
        migrations.RemoveField(model_name='profissional', name='ics_token'),
        migrations.RemoveField(model_name='listaespera', name='token_reserva'),
        migrations.RemoveField(model_name='listaespera', name='expira_em'),
    ]
