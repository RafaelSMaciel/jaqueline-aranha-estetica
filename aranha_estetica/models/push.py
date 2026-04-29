# app_shivazen/models/push.py — Web Push subscriptions
from django.conf import settings
from django.db import models


class WebPushSubscription(models.Model):
    """Subscription Web Push (VAPID) por usuario admin/profissional.

    endpoint, p256dh, auth: dados retornados pelo PushManager.subscribe().
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='push_subscriptions',
    )
    endpoint = models.URLField(max_length=600, unique=True)
    p256dh = models.CharField(max_length=140)
    auth = models.CharField(max_length=80)
    user_agent = models.CharField(max_length=300, blank=True, default='')
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    ultima_falha_em = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'web_push_subscription'
        indexes = [
            models.Index(fields=['user', 'ativo'], name='idx_pushsub_user_ativo'),
        ]

    def __str__(self):
        return f'PushSub user={self.user_id} endpoint={self.endpoint[:40]}'
