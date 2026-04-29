"""Mixins reutilizaveis para models."""
from django.db import models
from django.utils import timezone


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deletado_em__isnull=True)

    def dead(self):
        return self.filter(deletado_em__isnull=False)

    def delete(self):
        return self.update(deletado_em=timezone.now())


class SoftDeleteManager(models.Manager):
    """Manager que por padrao filtra soft-deleted."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).alive()


class AllObjectsManager(models.Manager):
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db)


class SoftDeleteMixin(models.Model):
    """Adiciona deletado_em + managers. Use `Model.objects` para ativos e `Model.all_objects` para tudo."""

    deletado_em = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        self.deletado_em = timezone.now()
        self.save(update_fields=["deletado_em"])

    def hard_delete(self, using=None, keep_parents=False):
        super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        self.deletado_em = None
        self.save(update_fields=["deletado_em"])


class TimestampedMixin(models.Model):
    """criado_em + atualizado_em."""

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
