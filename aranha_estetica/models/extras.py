"""Financeiro auxiliar: carteira de credito do cliente + comissao de profissional.

Remodelagem v2.1 (docs/specs/remodelagem-banco-v2.md): os 8 models especulativos
(patch test, fotos, estoque, tags, plano de tratamento) foram removidos — zero uso
em views/services/admin. Historico no git se precisar ressuscitar.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from .mixins import TimestampedMixin
from .clientes import Cliente
from .procedimentos import Procedimento
from .profissionais import Profissional


# ═══════════════════════════════════════
#  CREDITO CLIENTE (saldo)
# ═══════════════════════════════════════

class Carteira(TimestampedMixin):
    """Saldo do cliente (vale presente, credito de cancelamento, refund)."""
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, related_name='carteira')
    saldo = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
    )

    class Meta:
        managed = True
        db_table = 'carteira'

    def __str__(self):
        return f'Credito {self.cliente.nome_completo}: R$ {self.saldo}'


class MovimentoCarteira(models.Model):
    """Entrada/saida do credito do cliente."""
    TIPO_CHOICES = [
        ('CREDITO', 'Credito (vale, refund, ajuste+)'),
        ('DEBITO', 'Debito (uso, ajuste-)'),
    ]
    ORIGEM_CHOICES = [
        ('VALE_PRESENTE', 'Vale presente'),
        ('CANCELAMENTO', 'Refund cancelamento'),
        ('AJUSTE_MANUAL', 'Ajuste manual'),
        ('USO_AGENDAMENTO', 'Uso em agendamento'),
        ('CASHBACK_INDICACAO', 'Cashback por indicacao'),
        ('CASHBACK_ESTORNO', 'Estorno cashback'),
        ('OUTRO', 'Outro'),
    ]

    carteira = models.ForeignKey(Carteira, on_delete=models.CASCADE, related_name='movimentos')
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    origem = models.CharField(max_length=30, choices=ORIGEM_CHOICES, default='OUTRO')
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    saldo_resultante = models.DecimalField(max_digits=10, decimal_places=2)
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.SET_NULL, blank=True, null=True,
    )
    observacoes = models.TextField(blank=True, default='')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'movimento_carteira'
        indexes = [
            models.Index(fields=['carteira', '-criado_em'], name='idx_movcart_cart'),
            models.Index(fields=['origem', 'tipo'], name='idx_movcred_origem_tipo'),
        ]
        constraints = [
            # Idempotencia F-CSB: 1 unico CASHBACK_INDICACAO por (credito, atendimento)
            models.UniqueConstraint(
                fields=['carteira', 'atendimento'],
                condition=models.Q(origem='CASHBACK_INDICACAO'),
                name='uniq_cashback_indicacao_por_atendimento',
            ),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} R$ {self.valor} ({self.origem})'


# ═══════════════════════════════════════
#  COMISSAO PROFISSIONAL
# ═══════════════════════════════════════

class RegraComissao(TimestampedMixin):
    """Regra de calculo de comissao por (procedimento, profissional).

    Suporta percentual OU fixo. Profissional opcional (NULL = aplica a todos).
    Procedimento opcional (NULL = aplica a todos). Combinacao mais especifica vence.
    """
    profissional = models.ForeignKey(
        Profissional, on_delete=models.CASCADE, blank=True, null=True,
        related_name='regras_comissao',
    )
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.CASCADE, blank=True, null=True,
        related_name='regras_comissao',
    )
    percentual = models.DecimalField(
        max_digits=5, decimal_places=2, blank=True, null=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text='Ex: 30.00 para 30%. Se preenchido, valor deve ser nulo.',
    )
    valor = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text='Valor fixo R$. Se preenchido, percentual deve ser nulo.',
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'regra_comissao'
        constraints = [
            models.CheckConstraint(
                check=(
                    (
                        models.Q(percentual__isnull=False) & models.Q(valor__isnull=True)
                    ) | (
                        models.Q(percentual__isnull=True) & models.Q(valor__isnull=False)
                    )
                ),
                name='chk_regra_comissao_percentual_xor_fixo',
            ),
        ]
        indexes = [
            models.Index(fields=['profissional', 'procedimento', 'ativo'],
                         name='idx_regra_comissao_lookup'),
        ]

    def __str__(self):
        prof = self.profissional.nome if self.profissional_id else 'qualquer'
        proc = self.procedimento.nome if self.procedimento_id else 'qualquer'
        valor = f'{self.percentual}%' if self.percentual else f'R$ {self.valor}'
        return f'{prof}/{proc}: {valor}'


class MovimentoComissao(models.Model):
    """Comissao gerada/paga p/ profissional em atendimento concluido."""

    STATUS_PENDENTE = 'PENDENTE'
    STATUS_PAGA = 'PAGA'
    STATUS_ESTORNADA = 'ESTORNADA'
    STATUS_CHOICES = [
        (STATUS_PENDENTE, 'Pendente'),
        (STATUS_PAGA, 'Paga'),
        (STATUS_ESTORNADA, 'Estornada'),
    ]

    profissional = models.ForeignKey(
        Profissional, on_delete=models.PROTECT, related_name='comissoes',
    )
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.PROTECT, related_name='comissoes',
    )
    regra = models.ForeignKey(
        RegraComissao, on_delete=models.SET_NULL, blank=True, null=True,
    )
    valor = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_PENDENTE)
    pago_em = models.DateTimeField(blank=True, null=True)
    observacoes = models.TextField(blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'movimento_comissao'
        constraints = [
            # Idempotencia: 1 comissao ATIVA por atendimento (PENDENTE ou PAGA)
            models.UniqueConstraint(
                fields=['atendimento'],
                condition=models.Q(status__in=['PENDENTE', 'PAGA']),
                name='uniq_comissao_ativa_por_atendimento',
            ),
        ]
        indexes = [
            models.Index(fields=['profissional', 'status'], name='idx_comissao_prof_status'),
            models.Index(fields=['-criado_em'], name='idx_comissao_criado'),
        ]

    def __str__(self):
        return f'Comissao {self.profissional.nome} R$ {self.valor} ({self.get_status_display()})'
