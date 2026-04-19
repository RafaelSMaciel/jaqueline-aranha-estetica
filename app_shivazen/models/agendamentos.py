# app_shivazen/models/agendamentos.py — Atendimentos e notificacoes
import secrets
from django.core.validators import MinValueValidator
from django.db import models

from .clientes import Cliente
from .procedimentos import Procedimento, Promocao
from .profissionais import Profissional


class Atendimento(models.Model):
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente de Confirmação'),
        ('AGENDADO', 'Agendado'),
        ('CONFIRMADO', 'Confirmado'),
        ('REALIZADO', 'Realizado'),
        ('CANCELADO', 'Cancelado'),
        ('FALTOU', 'Faltou'),
        ('REAGENDADO', 'Reagendado'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.RESTRICT)
    profissional = models.ForeignKey(Profissional, on_delete=models.RESTRICT)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.RESTRICT)
    promocao = models.ForeignKey(
        Promocao, on_delete=models.SET_NULL, blank=True, null=True
    )
    reagendado_de = models.ForeignKey(
        'self', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='reagendamentos'
    )
    data_hora_inicio = models.DateTimeField()
    data_hora_fim = models.DateTimeField()
    valor_cobrado = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        validators=[MinValueValidator(0)],
    )
    valor_original = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        validators=[MinValueValidator(0)],
    )
    descricao_preco = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, default='PENDENTE', choices=STATUS_CHOICES)
    token_cancelamento = models.CharField(
        max_length=64, unique=True, blank=True, null=True, db_index=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True, null=True)

    def save(self, *args, **kwargs):
        if not self.token_cancelamento:
            self.token_cancelamento = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    class Meta:
        managed = True
        db_table = 'atendimento'
        indexes = [
            models.Index(fields=['status'], name='idx_atendimento_status'),
            models.Index(fields=['data_hora_inicio'], name='idx_atendimento_data'),
            models.Index(fields=['cliente', 'status'], name='idx_atendimento_cli_status'),
            models.Index(fields=['cliente'], name='idx_atendimento_cliente'),
            models.Index(fields=['profissional', 'data_hora_inicio'], name='idx_atend_prof_data'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(status__in=[
                    'PENDENTE', 'AGENDADO', 'CONFIRMADO', 'REALIZADO',
                    'CANCELADO', 'FALTOU', 'REAGENDADO',
                ]),
                name='chk_atendimento_status_v2'
            ),
            models.CheckConstraint(
                check=models.Q(data_hora_fim__gt=models.F('data_hora_inicio')),
                name='chk_atendimento_fim_apos_inicio',
            ),
        ]

    def __str__(self):
        data_fmt = self.data_hora_inicio.strftime('%d/%m/%Y %H:%M') if self.data_hora_inicio else 's/ data'
        cliente_nome = self.cliente.nome_completo if self.cliente_id else 's/ cliente'
        proc_nome = self.procedimento.nome if self.procedimento_id else 's/ procedimento'
        return f'{data_fmt} — {cliente_nome} ({proc_nome})'


class Notificacao(models.Model):
    TIPO_CHOICES = [
        ('LEMBRETE', 'Lembrete D-1'),
        ('LEMBRETE_2H', 'Lembrete T-2h'),
        ('CONFIRMACAO', 'Confirmação'),
        ('CANCELAMENTO', 'Cancelamento'),
        ('NPS', 'Pesquisa NPS'),
        ('APROVACAO', 'Aprovação Profissional'),
    ]
    CANAL_CHOICES = [
        ('WHATSAPP', 'WhatsApp'),
        ('SMS', 'SMS'),
        ('EMAIL', 'E-mail'),
    ]
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('ENVIADO', 'Enviado'),
        ('FALHOU', 'Falhou'),
    ]
    RESPOSTA_CHOICES = [
        ('CONFIRMOU', 'Confirmou'),
        ('CANCELOU', 'Cancelou'),
    ]

    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=30, default='LEMBRETE', choices=TIPO_CHOICES)
    canal = models.CharField(max_length=20, default='WHATSAPP', choices=CANAL_CHOICES)
    status_envio = models.CharField(max_length=20, default='PENDENTE', choices=STATUS_CHOICES)
    resposta_cliente = models.CharField(
        max_length=20, blank=True, null=True, choices=RESPOSTA_CHOICES
    )
    token = models.CharField(max_length=64, unique=True, blank=True, null=True)
    mensagem = models.TextField(blank=True, null=True)
    enviado_em = models.DateTimeField(blank=True, null=True)
    respondido_em = models.DateTimeField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'notificacao'
        indexes = [
            models.Index(fields=['tipo', 'status_envio'], name='idx_notificacao_tipo_status'),
            models.Index(fields=['-criado_em'], name='idx_notificacao_criado'),
            models.Index(fields=['tipo', 'canal', 'status_envio', '-criado_em'], name='idx_notif_nps_lookup'),
            models.Index(fields=['atendimento', 'tipo'], name='idx_notif_atend_tipo'),
        ]

    def __str__(self):
        data_fmt = self.criado_em.strftime('%d/%m/%Y %H:%M') if self.criado_em else 's/ data'
        cliente_nome = (
            self.atendimento.cliente.nome_completo
            if self.atendimento_id and self.atendimento.cliente_id
            else 's/ cliente'
        )
        return f'{self.get_tipo_display()} — {cliente_nome} ({data_fmt})'
