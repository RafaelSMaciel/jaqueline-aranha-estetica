"""Mixins e campos de auditoria de linha reutilizaveis (padrao SQL §1.2).

Metadados de linha no banco em ingles desde a 0079 (created_at, updated_at, deleted_at,
created_by_id, created_by_name); o atributo Python continua o do dominio (criado_em, ...).
"""
from django.db import models
from django.db.models.functions import Now

# COMMENT das colunas de auditoria acrescentadas a tabelas antigas pela 0054 (spec §3.2, §7.2):
# o banco nao sabe quando a linha legada nasceu/mudou — o valor e o instante da migration.
CRIADO_EM_LEGADO = 'Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).'
ATUALIZADO_EM_LEGADO = 'Linhas anteriores a 0054 comecam com o instante da migration.'
ATUALIZADO_EM_BACKFILL = 'NULL legado preenchido com created_at na 0054.'
# updated_at acrescentado pela 0079 (codigo_otp, execucao_job): a linha antiga recebe o ultimo evento
# conhecido (uso/conclusao) ou a criacao
ATUALIZADO_EM_0079 = 'Linhas anteriores a 0079 receberam o ultimo evento conhecido (uso/conclusao) ou a criacao.'


def criado_em_legado():
    """created_at de tabela que nao tinha a coluna (0054): DEFAULT now() + COMMENT."""
    return models.DateTimeField(
        auto_now_add=True, db_default=Now(), db_comment=CRIADO_EM_LEGADO, db_column='created_at',
    )


def atualizado_em_legado():
    """updated_at de tabela que nao tinha a coluna (0054): DEFAULT now() + COMMENT."""
    return models.DateTimeField(
        auto_now=True, db_default=Now(), db_comment=ATUALIZADO_EM_LEGADO, db_column='updated_at',
    )


class TimestampedMixin(models.Model):
    """created_at + updated_at (DEFAULT now() no banco: procedures/triggers inserem por SQL)."""

    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now(), db_column='created_at')
    atualizado_em = models.DateTimeField(auto_now=True, db_default=Now(), db_column='updated_at')

    class Meta:
        abstract = True
