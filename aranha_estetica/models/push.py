# aranha_estetica/models/push.py — Web Push subscriptions
from django.conf import settings
from django.db import models
from django.db.models.functions import Now

from ..db.indices import Indice
from .mixins import atualizado_em_legado


class AssinaturaPush(models.Model):
    """Subscription Web Push (VAPID) por usuario admin/profissional.

    endpoint, p256dh, auth: dados retornados pelo PushManager.subscribe().
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='push_subscriptions', db_column='usuario_id', db_index=False,
    )
    endpoint = models.URLField(max_length=600)
    p256dh = models.CharField(
        max_length=140, db_column='chave_p256dh', db_comment='Chave publica do navegador (PushManager.subscribe).',
    )
    auth = models.CharField(
        max_length=80, db_column='chave_auth',
        db_comment='Segredo de autenticacao do navegador (PushManager.subscribe).',
    )
    user_agent = models.CharField(max_length=300, blank=True, default='', db_default='')
    ativo = models.BooleanField(default=True, db_default=True, db_column='eh_ativo')
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    atualizado_em = atualizado_em_legado()
    ultima_falha_em = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'assinatura_push'
        db_table_comment = 'Inscricao Web Push (VAPID) de um usuario da equipe.'
        constraints = [
            models.UniqueConstraint(fields=['endpoint'], name='uq_assinatura_push_endpoint'),
        ]
        indexes = [
            Indice(fields=['user'], name='ix_assinatura_push_usuario'),
        ]

    def __str__(self):
        return f'PushSub user={self.user_id} endpoint={self.endpoint[:40]}'
