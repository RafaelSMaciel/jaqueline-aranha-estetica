# aranha_estetica/models/historico.py — Trilha de DADOS gravada por trigger
"""HistoricoAlteracao (docs/specs/padrao-sql.md §2.1 e §5.3 — migration 0056).

Uma linha por INSERT/UPDATE/DELETE nas 11 tabelas sensiveis (prontuario,
atendimento, compra_pacote, carteira, regra_comissao, preco, promocao, pacote,
versao_termo, usuario, configuracao), gravada pelo trigger ``tg_<t>_historico``
(``fn_registrar_historico``) — pega tambem o que nao passa pela aplicacao
(Django admin, SQL manual, procedures). UPDATE guarda so as colunas alteradas
(antes/depois); ``usuario.senha_hash`` aparece como ``"[redigido]"``.

Autor/IP/origem vem das GUCs da sessao (``app.usuario_id``, ``app.ip_origem``,
``app.origem``) que o Ator da borda (``ator.atuando``: middleware, cron, ``rodar_job``) e o
``migrate_atomico`` definem (``utils/banco.definir_contexto_banco``). Sem contexto (SQL manual) =
autor NULL e origem ''.

So existe conteudo no Postgres (o SQLite dos testes nao tem triggers). Append-only:
no PG ``tg_historico_alteracao_imutavel`` (so o SET NULL do autor passa; DELETE so
na purga de retencao, ``sp_purgar_dados_expirados``); no Python o QuerySet/model
recusam (o Django admin e somente leitura, com trilha de leitura).
Retencao: 20 anos p/ prontuario (registro clinico), 5 anos p/ o resto.
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now

from ..db.indices import Indice

MSG_IMUTAVEL = 'Histórico de alterações é trilha append-only: não pode ser alterado nem excluído.'

# Quais tabelas tem trilha e quais colunas sao redigidas: fonte unica no trigger
# (migrations/sql/v0056_triggers.TABELAS_HISTORICO).


class HistoricoAlteracaoQuerySet(models.QuerySet):
    # update() NAO e recusado: o SET_NULL de usuario_autor (Collector do Django) usa
    # QuerySet.update — e a unica mutacao que o trigger aceita (como ProntuarioVersao).

    def delete(self):
        raise ValidationError(MSG_IMUTAVEL)

    delete.alters_data = True
    delete.queryset_only = True

    def do_registro(self, tabela, pk):
        return self.filter(tabela=tabela, registro_pk=pk).order_by('-criado_em', '-id')


class HistoricoAlteracao(models.Model):
    OPERACAO_INSERT = 'INSERT'
    OPERACAO_UPDATE = 'UPDATE'
    OPERACAO_DELETE = 'DELETE'
    OPERACAO_CHOICES = [
        (OPERACAO_INSERT, 'Inclusão'),
        (OPERACAO_UPDATE, 'Alteração'),
        (OPERACAO_DELETE, 'Exclusão'),
    ]

    tabela = models.CharField(max_length=63)
    registro_pk = models.BigIntegerField(
        db_comment='PK do registro alterado na tabela indicada em "tabela" (referencia polimorfica, sem FK).',
    )
    operacao = models.CharField(max_length=6, choices=OPERACAO_CHOICES)
    colunas_alteradas = models.JSONField(
        default=list, db_default=[], blank=True,
        db_comment='UPDATE: colunas que mudaram (ordem alfabetica). INSERT/DELETE: lista vazia.',
    )
    dados_antes = models.JSONField(
        null=True, blank=True,
        db_comment='UPDATE: valores anteriores so das colunas alteradas; DELETE: a linha inteira.',
    )
    dados_depois = models.JSONField(
        null=True, blank=True,
        db_comment='UPDATE: valores novos so das colunas alteradas; INSERT: a linha inteira.',
    )
    # sem indice de FK proprio: ix_historico_alteracao_usuario_autor_parcial (1a coluna) cobre
    usuario_autor = models.ForeignKey(
        'Usuario', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        db_index=False, db_column='created_by_id',
    )
    ip_origem = models.GenericIPAddressField(blank=True, null=True)
    origem = models.CharField(
        max_length=60, blank=True, default='', db_default='',
        db_comment='current_setting(app.origem): web, cron:<job>, comando:<job>, migracao; vazio = SQL manual.',
    )
    transacao_ref = models.BigIntegerField(
        db_comment='pg_current_xact_id() - agrupa as linhas de uma mesma transacao/request.',
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now(), db_column='created_at')

    objects = HistoricoAlteracaoQuerySet.as_manager()

    class Meta:
        db_table = 'historico_alteracao'
        db_table_comment = (
            'Trilha de dados gravada por trigger (fn_registrar_historico) nas tabelas sensiveis: antes/depois '
            'so das colunas alteradas. Append-only (tg_historico_alteracao_imutavel). Retencao: 20 anos p/ '
            'prontuario, 5 anos p/ o resto.'
        )
        verbose_name = 'histórico de alteração'
        verbose_name_plural = 'histórico de alterações'
        constraints = [
            models.CheckConstraint(
                condition=Q(operacao__in=['INSERT', 'UPDATE', 'DELETE']),
                name='ck_historico_alteracao_operacao',
            ),
            models.CheckConstraint(
                condition=(
                    Q(operacao='INSERT', dados_antes__isnull=True, dados_depois__isnull=False)
                    | Q(operacao='UPDATE', dados_antes__isnull=False, dados_depois__isnull=False)
                    | Q(operacao='DELETE', dados_antes__isnull=False, dados_depois__isnull=True)
                ),
                name='ck_historico_alteracao_dados_coerentes',
            ),
            # PG (0056): ck_historico_alteracao_colunas_alteradas_lista (jsonb_typeof = 'array')
        ]
        indexes = [
            Indice(
                fields=['tabela', 'registro_pk', '-criado_em'], name='ix_historico_alteracao_tabela_registro',
            ),
            Indice(fields=['criado_em'], name='ix_historico_alteracao_created_at'),
            Indice(
                fields=['usuario_autor', '-criado_em'], condition=Q(usuario_autor__isnull=False),
                name='ix_historico_alteracao_created_by_parcial',
            ),
        ]

    def __str__(self):
        return f'{self.get_operacao_display()} {self.tabela} #{self.registro_pk}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(MSG_IMUTAVEL)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(MSG_IMUTAVEL)
