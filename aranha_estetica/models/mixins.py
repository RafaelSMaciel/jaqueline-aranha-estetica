"""Mixins e campos de auditoria de linha reutilizaveis (padrao SQL §1.2)."""
from django.db import models
from django.db.models.functions import Now

# COMMENT das colunas de auditoria acrescentadas a tabelas antigas pela 0054 (spec §3.2, §7.2):
# o banco nao sabe quando a linha legada nasceu/mudou — o valor e o instante da migration.
CRIADO_EM_LEGADO = 'Linhas anteriores a 0054 receberam o instante da migration (criacao real desconhecida).'
ATUALIZADO_EM_LEGADO = 'Linhas anteriores a 0054 comecam com o instante da migration.'
ATUALIZADO_EM_BACKFILL = 'NULL legado preenchido com criado_em na 0054.'


def criado_em_legado():
    """criado_em de tabela que nao tinha a coluna (0054): DEFAULT now() + COMMENT."""
    return models.DateTimeField(auto_now_add=True, db_default=Now(), db_comment=CRIADO_EM_LEGADO)


def atualizado_em_legado():
    """atualizado_em de tabela que nao tinha a coluna (0054): DEFAULT now() + COMMENT."""
    return models.DateTimeField(auto_now=True, db_default=Now(), db_comment=ATUALIZADO_EM_LEGADO)


class TimestampedMixin(models.Model):
    """criado_em + atualizado_em (DEFAULT now() no banco: procedures/triggers inserem por SQL)."""

    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    atualizado_em = models.DateTimeField(auto_now=True, db_default=Now())

    class Meta:
        abstract = True
