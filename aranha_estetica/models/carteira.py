"""Carteira de credito da cliente (cashback de indicacao, uso e devolucao) — livro-razao."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models.functions import Now

from ..db.indices import Indice
from .clientes import Cliente
from .mixins import TimestampedMixin


class Carteira(TimestampedMixin):
    """Saldo de credito da cliente (programa de indicacao: credito, uso no atendimento, devolucao).

    REGRA: toda mutacao de saldo exige select_for_update() + transaction.atomic.
    O CHECK abaixo pega qualquer lost-update que escapar do lock.
    PROTECT: historico financeiro nao cascateia com o cliente (anonimizar, nao apagar).
    """
    cliente = models.OneToOneField(Cliente, on_delete=models.PROTECT, related_name='carteira')
    saldo = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'), db_default=Decimal('0'), db_column='valor_saldo',
    )

    class Meta:
        managed = True
        db_table = 'carteira'
        db_table_comment = (
            'Saldo de credito do cliente (cashback, vale). Toda mutacao: select_for_update + movimento_carteira '
            'na mesma transacao.'
        )
        constraints = [
            models.CheckConstraint(
                condition=models.Q(saldo__gte=0),
                name='ck_carteira_valor_saldo_nao_negativo',
            ),
        ]

    def __str__(self):
        return f'Credito {self.cliente.nome}: R$ {self.saldo}'


class TipoMovimentoCarteira(models.TextChoices):
    CREDITO = 'CREDITO', 'Crédito'
    DEBITO = 'DEBITO', 'Débito'


class OrigemMovimentoCarteira(models.TextChoices):
    """So origens com fluxo (0067 tirou VALE_PRESENTE e OUTRO, que nada gerava). Reembolso de pacote
    e so em dinheiro: nao vira credito de carteira."""
    CANCELAMENTO = 'CANCELAMENTO', 'Devolução (atendimento não realizado)'
    AJUSTE_MANUAL = 'AJUSTE_MANUAL', 'Ajuste manual'
    USO_AGENDAMENTO = 'USO_AGENDAMENTO', 'Uso em atendimento'
    CASHBACK_INDICACAO = 'CASHBACK_INDICACAO', 'Crédito por indicação'
    CASHBACK_ESTORNO = 'CASHBACK_ESTORNO', 'Estorno do crédito por indicação'


class MovimentoCarteira(models.Model):
    """Entrada/saida do credito do cliente.

    Ledger append-only: no PG o trigger tg_movimento_carteira_imutavel (0038; nome desde a 0054)
    bloqueia UPDATE/DELETE. Por isso todas as FKs sao PROTECT — SET_NULL/CASCADE
    virariam UPDATE/DELETE e estourariam InternalError (500) no admin; com
    PROTECT o Django mostra a tela de "objetos protegidos".
    """
    Tipo = TipoMovimentoCarteira
    Origem = OrigemMovimentoCarteira
    TIPO_CHOICES = TipoMovimentoCarteira.choices
    ORIGEM_CHOICES = OrigemMovimentoCarteira.choices

    # FKs sem indice automatico: carteira/atendimento cobertos pelos ix_; usuario = autoria (§2.11)
    carteira = models.ForeignKey(Carteira, on_delete=models.PROTECT, related_name='movimentos', db_index=False)
    tipo = models.CharField(max_length=10, choices=TipoMovimentoCarteira.choices)
    # sem default: todo movimento diz de onde veio
    origem = models.CharField(max_length=30, choices=OrigemMovimentoCarteira.choices)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    saldo_resultante = models.DecimalField(max_digits=10, decimal_places=2, db_column='valor_saldo_resultante')
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.PROTECT, blank=True, null=True, db_index=False,
    )
    observacoes = models.TextField(blank=True, default='', db_default='', db_column='observacao')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True,
        db_column='created_by_id', db_index=False,
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now(), db_column='created_at')

    class Meta:
        managed = True
        db_table = 'movimento_carteira'
        db_table_comment = 'Ledger append-only da carteira (tg_movimento_carteira_imutavel). Estorno = novo movimento.'
        indexes = [
            Indice(fields=['carteira', '-criado_em'], name='ix_movimento_carteira_carteira_created_at'),
            Indice(
                fields=['atendimento'], condition=models.Q(atendimento__isnull=False),
                name='ix_movimento_carteira_atendimento_parcial',
            ),
        ]
        constraints = [
            # Idempotencia F-CSB: 1 unico CASHBACK_INDICACAO por (credito, atendimento)
            models.UniqueConstraint(
                fields=['carteira', 'atendimento'],
                condition=models.Q(origem=OrigemMovimentoCarteira.CASHBACK_INDICACAO),
                name='uq_movimento_carteira_cashback_por_atendimento',
            ),
            models.CheckConstraint(
                condition=models.Q(tipo__in=TipoMovimentoCarteira.values), name='ck_movimento_carteira_tipo',
            ),
            models.CheckConstraint(
                condition=models.Q(origem__in=OrigemMovimentoCarteira.values), name='ck_movimento_carteira_origem',
            ),
            models.CheckConstraint(condition=models.Q(valor__gt=0), name='ck_movimento_carteira_valor_positivo'),
            models.CheckConstraint(
                condition=models.Q(saldo_resultante__gte=0),
                name='ck_movimento_carteira_valor_saldo_resultante_nao_negativo',
            ),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} R$ {self.valor} ({self.origem})'
