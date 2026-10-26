"""Financeiro auxiliar: carteira de credito do cliente + comissao de profissional.

Remodelagem v2.1 (docs/specs/remodelagem-banco-v2.md): os 8 models especulativos
(patch test, fotos, estoque, tags, plano de tratamento) foram removidos — zero uso
em views/services/admin. Historico no git se precisar ressuscitar.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Case, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Now
from django.utils import timezone

from ..db.indices import Indice

from .mixins import TimestampedMixin
from .clientes import Cliente
from .procedimentos import Procedimento
from .profissionais import Profissional


# ═══════════════════════════════════════
#  CREDITO CLIENTE (saldo)
# ═══════════════════════════════════════

class Carteira(TimestampedMixin):
    """Saldo do cliente (vale presente, credito de cancelamento, refund).

    REGRA: toda mutacao de saldo exige select_for_update() + transaction.atomic.
    O CHECK abaixo pega qualquer lost-update que escapar do lock.
    PROTECT: historico financeiro nao cascateia com o cliente (anonimizar, nao apagar).
    """
    cliente = models.OneToOneField(Cliente, on_delete=models.PROTECT, related_name='carteira')
    saldo = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
    )

    class Meta:
        managed = True
        db_table = 'carteira'
        constraints = [
            models.CheckConstraint(
                check=models.Q(saldo__gte=0),
                name='chk_carteira_saldo_nao_negativo',
            ),
        ]

    def __str__(self):
        return f'Credito {self.cliente.nome}: R$ {self.saldo}'


class MovimentoCarteira(models.Model):
    """Entrada/saida do credito do cliente.

    Ledger append-only: no PG o trigger trg_movimento_carteira_imutavel (0038)
    bloqueia UPDATE/DELETE. Por isso todas as FKs sao PROTECT — SET_NULL/CASCADE
    virariam UPDATE/DELETE e estourariam InternalError (500) no admin; com
    PROTECT o Django mostra a tela de "objetos protegidos".
    """
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

    carteira = models.ForeignKey(Carteira, on_delete=models.PROTECT, related_name='movimentos')
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    origem = models.CharField(max_length=30, choices=ORIGEM_CHOICES, default='OUTRO')
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    saldo_resultante = models.DecimalField(max_digits=10, decimal_places=2)
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.PROTECT, blank=True, null=True,
    )
    observacoes = models.TextField(blank=True, default='')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True,
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
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('100.00'))],
        help_text='Ex: 30.00 para 30% (0 a 100). Se preenchido, valor deve ser nulo.',
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
            # comissao nao passa do valor do atendimento (espelha chk_promocao_desconto_0_100)
            models.CheckConstraint(
                check=(
                    models.Q(percentual__isnull=True) |
                    (models.Q(percentual__gte=0) & models.Q(percentual__lte=100))
                ),
                name='chk_regra_comissao_percentual_0_100',
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


MSG_COMISSAO_IMUTAVEL = (
    'Comissão é livro-razão append-only: pagamento e estorno são lançamentos novos '
    '(ComissaoService.marcar_paga / estornar_comissao).'
)


class MovimentoComissaoQuerySet(models.QuerySet):
    """Ledger append-only: UPDATE/DELETE recusados (no PG, tg_movimento_comissao_imutavel)."""

    def update(self, **kwargs):
        raise ValidationError(MSG_COMISSAO_IMUTAVEL)

    update.alters_data = True
    update.queryset_only = True

    def delete(self):
        raise ValidationError(MSG_COMISSAO_IMUTAVEL)

    delete.alters_data = True
    delete.queryset_only = True

    def creditos(self):
        return self.filter(tipo=MovimentoComissao.TIPO_CREDITO)

    def creditos_com_status(self):
        """1 linha por CREDITO com o status derivado (espelho de vw_comissao).

        Anota ``pago_em`` (PAGAMENTO), ``estornado_em`` (ESTORNO) e ``status``:
        ESTORNADA se ha estorno; senao PAGA se ha pagamento; senao PENDENTE. Os
        UNIQUE parciais garantem <= 1 pagamento e <= 1 estorno por credito.
        """
        filhos = MovimentoComissao.objects.filter(movimento_origem=OuterRef('pk'))
        return self.creditos().annotate(
            pago_em=Subquery(
                filhos.filter(tipo=MovimentoComissao.TIPO_PAGAMENTO).values('criado_em')[:1],
            ),
            estornado_em=Subquery(
                filhos.filter(tipo=MovimentoComissao.TIPO_ESTORNO).values('criado_em')[:1],
            ),
        ).annotate(
            status=Case(
                When(estornado_em__isnull=False, then=Value(MovimentoComissao.STATUS_ESTORNADA)),
                When(pago_em__isnull=False, then=Value(MovimentoComissao.STATUS_PAGA)),
                default=Value(MovimentoComissao.STATUS_PENDENTE),
                output_field=models.CharField(),
            ),
        )


class MovimentoComissao(models.Model):
    """Livro-razao de comissao (docs/specs/padrao-sql.md §2.9 e §4.f — migration 0053).

    CREDITO (atendimento realizado; 1 por atendimento), PAGAMENTO (baixa no
    painel) e ESTORNO (atendimento cancelado) — os dois ultimos referenciam o
    CREDITO (``movimento_origem``) e repetem profissional, atendimento e valor
    (sem baixa parcial). ``valor`` e sempre positivo; o sinal vem do tipo.
    Status e saldo sao DERIVADOS (``creditos_com_status``,
    ``services/comissao_service``): o saldo a pagar fica NEGATIVO quando uma
    comissao ja paga e estornada (desconta do proximo repasse).

    So-PG (0053): tg_movimento_comissao_validar (origem = CREDITO do mesmo
    profissional/atendimento/valor; nada de PAGAMENTO em credito estornado) e
    tg_movimento_comissao_imutavel. No SQLite o Python cobre (save/delete).
    """

    TIPO_CREDITO = 'CREDITO'
    TIPO_ESTORNO = 'ESTORNO'
    TIPO_PAGAMENTO = 'PAGAMENTO'
    TIPO_CHOICES = [
        (TIPO_CREDITO, 'Crédito (atendimento realizado)'),
        (TIPO_ESTORNO, 'Estorno (cancelamento)'),
        (TIPO_PAGAMENTO, 'Pagamento (baixa)'),
    ]
    # status DERIVADO do credito (nao e coluna): anotado por creditos_com_status()
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
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default=TIPO_CREDITO)
    # PROTECT: regra ja usada nao se exclui (o SET NULL bateria no trigger) — desative
    regra = models.ForeignKey(
        RegraComissao, on_delete=models.PROTECT, blank=True, null=True,
    )
    valor = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        db_comment='Sempre positivo; o sinal vem do tipo (CREDITO +, ESTORNO/PAGAMENTO -).',
    )
    # CREDITO que o ESTORNO/PAGAMENTO liquida. Sem indice automatico: coberto pelos
    # UNIQUE parciais e pelo ix_movimento_comissao_origem_parcial.
    movimento_origem = models.ForeignKey(
        'self', on_delete=models.PROTECT, blank=True, null=True,
        related_name='lancamentos_derivados', db_column='movimento_comissao_origem_id',
        db_index=False,
        db_comment='CREDITO que o ESTORNO/PAGAMENTO liquida (NULL no proprio CREDITO).',
    )
    observacoes = models.TextField(blank=True, default='')
    # autoria (quem deu baixa/estornou); sem indice: nenhuma tela filtra por autor (§2.11)
    usuario_autor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True,
        related_name='+', db_index=False,
    )
    criado_em = models.DateTimeField(default=timezone.now, db_default=Now(), editable=False)

    objects = MovimentoComissaoQuerySet.as_manager()

    class Meta:
        managed = True
        db_table = 'movimento_comissao'
        constraints = [
            models.CheckConstraint(
                condition=Q(tipo__in=['CREDITO', 'ESTORNO', 'PAGAMENTO']),
                name='ck_movimento_comissao_tipo',
            ),
            models.CheckConstraint(condition=Q(valor__gt=0), name='ck_movimento_comissao_valor_positivo'),
            models.CheckConstraint(
                condition=(
                    Q(tipo='CREDITO', movimento_origem__isnull=True)
                    | (~Q(tipo='CREDITO') & Q(movimento_origem__isnull=False))
                ),
                name='ck_movimento_comissao_origem_coerente',
            ),
            models.CheckConstraint(
                condition=Q(tipo='CREDITO') | Q(regra__isnull=True),
                name='ck_movimento_comissao_regra_so_credito',
            ),
            # idempotencia: nunca 2 creditos p/ o mesmo atendimento (nem depois de estornado)
            models.UniqueConstraint(
                fields=['atendimento'], condition=Q(tipo='CREDITO'),
                name='uq_movimento_comissao_credito_por_atendimento',
            ),
            models.UniqueConstraint(
                fields=['movimento_origem'], condition=Q(tipo='ESTORNO'),
                name='uq_movimento_comissao_estorno_por_origem',
            ),
            models.UniqueConstraint(
                fields=['movimento_origem'], condition=Q(tipo='PAGAMENTO'),
                name='uq_movimento_comissao_pagamento_por_origem',
            ),
        ]
        indexes = [
            models.Index(fields=['-criado_em'], name='idx_comissao_criado'),
            Indice(fields=['profissional', '-criado_em'], name='ix_movimento_comissao_profissional_criado_em'),
            Indice(
                fields=['movimento_origem', 'tipo'], condition=Q(movimento_origem__isnull=False),
                name='ix_movimento_comissao_origem_parcial',
            ),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} {self.profissional.nome} R$ {self.valor}'

    # ─── status derivado ────────────────────────────────────────────────
    # status / pago_em / estornado_em: anotados por creditos_com_status(); sem a
    # anotacao, lidos do ledger (so p/ CREDITO). refresh_from_db() descarta o valor.
    _DERIVADOS = ('_status', '_pago_em', '_estornado_em')

    def _lancamentos(self) -> dict:
        if self.tipo != self.TIPO_CREDITO or self.pk is None:
            return {}
        return dict(self.lancamentos_derivados.values_list('tipo', 'criado_em'))

    @property
    def status(self):
        if '_status' in self.__dict__:
            return self.__dict__['_status']
        if self.tipo != self.TIPO_CREDITO or self.pk is None:
            return None
        lancamentos = self._lancamentos()
        if self.TIPO_ESTORNO in lancamentos:
            return self.STATUS_ESTORNADA
        return self.STATUS_PAGA if self.TIPO_PAGAMENTO in lancamentos else self.STATUS_PENDENTE

    @status.setter
    def status(self, valor):
        self.__dict__['_status'] = valor

    @property
    def pago_em(self):
        if '_pago_em' in self.__dict__:
            return self.__dict__['_pago_em']
        return self._lancamentos().get(self.TIPO_PAGAMENTO)

    @pago_em.setter
    def pago_em(self, valor):
        self.__dict__['_pago_em'] = valor

    @property
    def estornado_em(self):
        if '_estornado_em' in self.__dict__:
            return self.__dict__['_estornado_em']
        return self._lancamentos().get(self.TIPO_ESTORNO)

    @estornado_em.setter
    def estornado_em(self, valor):
        self.__dict__['_estornado_em'] = valor

    def refresh_from_db(self, *args, **kwargs):
        for nome in self._DERIVADOS:
            self.__dict__.pop(nome, None)
        super().refresh_from_db(*args, **kwargs)

    def get_status_display(self):
        return dict(self.STATUS_CHOICES).get(self.status, self.status or '')

    # ─── append-only ────────────────────────────────────────────────────
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(MSG_COMISSAO_IMUTAVEL)
        if self.tipo != self.TIPO_CREDITO:
            self._validar_lancamento()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(MSG_COMISSAO_IMUTAVEL)

    def _validar_lancamento(self):
        """Espelho Python de tg_movimento_comissao_validar (o banco garante no PG)."""
        origem = (
            MovimentoComissao.objects.filter(pk=self.movimento_origem_id)
            .values('tipo', 'profissional_id', 'atendimento_id', 'valor').first()
        )
        if origem is None:
            return  # a FK / o CHECK de origem acusam
        if origem['tipo'] != self.TIPO_CREDITO:
            raise ValidationError(f'{self.tipo} deve referenciar um CRÉDITO de comissão.')
        if (origem['profissional_id'], origem['atendimento_id'], origem['valor']) != (
            self.profissional_id, self.atendimento_id, Decimal(self.valor),
        ):
            raise ValidationError(
                f'{self.tipo} deve repetir profissional, atendimento e valor do crédito (sem baixa parcial).'
            )
        if self.tipo == self.TIPO_PAGAMENTO and MovimentoComissao.objects.filter(
            movimento_origem_id=self.movimento_origem_id, tipo=self.TIPO_ESTORNO,
        ).exists():
            raise ValidationError('Comissão estornada não pode ser paga.')
