"""Infraestrutura: healthcheck, cron HTTP, webhooks (WhatsApp/Zenvia) e Web Push."""
from django.urls import path

from ..views.infra import cron, healthcheck, webhooks, webpush

urlpatterns = [
    # ─── Web Push ───
    path('webpush/public-key/', webpush.webpush_public_key, name='webpush_public_key'),
    path('webpush/subscribe/', webpush.webpush_subscribe, name='webpush_subscribe'),
    path('webpush/unsubscribe/', webpush.webpush_unsubscribe, name='webpush_unsubscribe'),
    # ─── Healthcheck ───
    path('health/', healthcheck.healthcheck, name='healthcheck'),  # readiness
    path('healthz/', healthcheck.liveness, name='liveness'),        # liveness
    # ─── Cron externo (cron-job.org) ───
    path('cron/run/<str:job_name>/', cron.run_job, name='cron_run_job'),
    # ─── WhatsApp Bot API ───
    path('api/whatsapp/webhook/', webhooks.whatsapp_webhook, name='whatsapp_webhook'),
    path('api/zenvia/webhook/', webhooks.zenvia_sms_webhook, name='zenvia_sms_webhook'),
]
