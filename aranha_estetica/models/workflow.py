# app_shivazen/models/workflow.py — Regras de workflow configuraveis
from django.db import models


class WorkflowRegra(models.Model):
    """Regra dinamica para disparar acoes em torno de eventos do atendimento.

    Trigger combinado com offset_minutos:
      ON_BOOK / ON_CANCEL / ON_NO_SHOW: disparado por signal apos transicao.
        offset_minutos ignorado.
      BEFORE_EVENT: disparado quando agora >= data_hora_inicio - |offset|.
        offset_minutos negativo ou positivo (modulo absoluto).
      AFTER_EVENT: disparado quando agora >= data_hora_fim + offset.

    Acao escolhe canal/efeito; template e variaveis em config_json.
    Engine deduplica via Notificacao(atendimento, tipo) ou WorkflowExecucao.
    """

    TRIGGER_CHOICES = [
        ('ON_BOOK', 'Ao agendar'),
        ('BEFORE_EVENT', 'Antes do evento'),
        ('AFTER_EVENT', 'Apos o evento'),
        ('ON_CANCEL', 'Ao cancelar'),
        ('ON_NO_SHOW', 'No-show registrado'),
    ]

    ACAO_CHOICES = [
        ('SEND_EMAIL', 'Enviar e-mail'),
        ('SEND_SMS', 'Enviar SMS'),
        ('SEND_WHATSAPP', 'Enviar WhatsApp'),
        ('SEND_PUSH', 'Web push'),
        ('WEBHOOK', 'Webhook HTTP'),
    ]

    nome = models.CharField(max_length=100)
    ativo = models.BooleanField(default=True)
    trigger = models.CharField(max_length=20, choices=TRIGGER_CHOICES)
    offset_minutos = models.IntegerField(default=0)
    acao = models.CharField(max_length=20, choices=ACAO_CHOICES)
    template = models.TextField(blank=True, default='')
    config_json = models.JSONField(default=dict, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        managed = True
        db_table = 'workflow_regra'
        indexes = [
            models.Index(fields=['ativo', 'trigger'], name='idx_workflow_ativo_trigger'),
        ]

    def __str__(self):
        return f'{self.nome} ({self.get_trigger_display()} -> {self.get_acao_display()})'


class WorkflowExecucao(models.Model):
    """Registro de execucao p/ deduplicacao + audit."""

    STATUS_CHOICES = [
        ('OK', 'Sucesso'),
        ('FALHOU', 'Falhou'),
        ('SKIPPED', 'Pulado (duplicado)'),
    ]

    regra = models.ForeignKey(WorkflowRegra, on_delete=models.CASCADE)
    atendimento = models.ForeignKey('Atendimento', on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    detalhe = models.TextField(blank=True, default='')
    executado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'workflow_execucao'
        indexes = [
            models.Index(fields=['regra', 'atendimento'], name='idx_wexec_regra_atend'),
            models.Index(fields=['-executado_em'], name='idx_wexec_executado'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['regra', 'atendimento'],
                name='uniq_wexec_regra_atend',
            ),
        ]

    def __str__(self):
        return f'{self.regra.nome} #{self.atendimento_id} {self.status}'
