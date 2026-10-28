"""Comissao da profissional: regra de calculo + livro-razao (credito, pagamento, estorno)."""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models
from django.db.models import Case, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Now
from django.utils import timezone

from ..db.indices import Indice
from .mixins import TimestampedMixin
from .procedimentos import Procedimento
from .profissionais import Profissional


class RegraComissao(TimestampedMixin):
    """Regra de calculo de comissao por (procedimento, profissional).

    Suporta percentual OU fixo. Profissional opcional (NULL = aplica a todos).
    Procedimento opcional (NULL = aplica a todos). Combinacao mais especifica vence.
    """
    # FKs sem indice automatico: os ix_ do Meta cobrem
    profissional = models.ForeignKey(
        Profissional, on_delete=models.CASCADE, blank=True, null=True,
        related_name='regras_comissao', db_index=False,
    )
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.CASCADE, blank=True, null=True,
        related_name='regras_comissao', db_index=False,
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
        db_column='valor_fixo',
    )
    ativo = models.BooleanField(default=True, db_default=True, db_column='eh_ativo')

    class Meta:
        managed = True
        db_table = 'regra_comissao'
        db_table_comment = (
            'Regra percentual XOR valor fixo por (profissional, procedimento); NULL = qualquer. Mais especifica '
            'vence; empate = atualizada por ultimo. Regra ja usada nao muda nem se exclui '
            '(tg_regra_comissao_validar, FK de movimento_comissao): desative e crie outra.'
        )
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(percentual__isnull=False) & models.Q(valor__isnull=True)
                    ) | (
                        models.Q(percentual__isnull=True) & models.Q(valor__isnull=False)
                    )
                ),
                name='ck_regra_comissao_percentual_xor_valor_fixo',
            ),
            # comissao nao passa do valor do atendimento (espelha ck_promocao_percentual_desconto_0_100)
            models.CheckConstraint(
                condition=(
                    models.Q(percentual__isnull=True) |
                    (models.Q(percentual__gte=0) & models.Q(percentual__lte=100))
                ),
                name='ck_regra_comissao_percentual_0_100',
            ),
            models.CheckConstraint(
                condition=models.Q(valor__isnull=True) | models.Q(valor__gte=0),
                name='ck_regra_comissao_valor_fixo_nao_negativo',
            ),
        ]
        indexes = [
            Indice(fields=['profissional', 'procedimento'], name='ix_regra_comissao_profissional_procedimento'),
            Indice(
                fields=['procedimento'], condition=models.Q(procedimento__isnull=False),
                name='ix_regra_comissao_procedimento_parcial',
            ),
        ]

    # o que define o calculo: regra ja usada em comissao nao muda isso (so desativa) — o CREDITO
    # antigo continua explicavel pela regra que ele cita. No PG: tg_regra_comissao_validar (0066).
    CAMPOS_DO_CALCULO = ('profissional_id', 'procedimento_id', 'percentual', 'valor')
    ESPECIFICIDADES = {
        3: 'Profissional + procedimento', 2: 'Profissional (qualquer procedimento)',
        1: 'Procedimento (qualquer profissional)', 0: 'Geral (qualquer um)',
    }

    def __str__(self):
        prof = self.profissional.nome if self.profissional_id else 'qualquer'
        proc = self.procedimento.nome if self.procedimento_id else 'qualquer'
        valor = f'{self.percentual}%' if self.percentual is not None else f'R$ {self.valor}'
        return f'{prof}/{proc}: {valor}'

    @property
    def especificidade(self) -> int:
        """3 = profissional+procedimento ... 0 = geral (a mais especifica vence: ComissaoService)."""
        return (2 if self.profissional_id else 0) + (1 if self.procedimento_id else 0)

    def get_especificidade_display(self) -> str:
        return self.ESPECIFICIDADES[self.especificidade]

    def foi_usada(self) -> bool:
        return self.pk is not None and MovimentoComissao.objects.filter(regra_id=self.pk).exists()

    def save(self, *args, **kwargs):
        if not self._state.adding and self.foi_usada():
            antes = RegraComissao.objects.filter(pk=self.pk).values(*self.CAMPOS_DO_CALCULO).first()
            if antes and any(antes[c] != getattr(self, c) for c in self.CAMPOS_DO_CALCULO):
                raise ValidationError(MSG_REGRA_USADA)
        super().save(*args, **kwargs)


MSG_REGRA_USADA = (
    'Regra já usada em comissão não muda (o crédito antigo cita esta regra): desative-a e crie uma nova.'
)


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

    def creditos_ativos(self):
        """CREDITOs sem ESTORNO (no maximo 1 por atendimento — tg_movimento_comissao_validar, 0063)."""
        estornados = MovimentoComissao.objects.filter(tipo=MovimentoComissao.TIPO_ESTORNO).values(
            'movimento_origem_id')
        return self.creditos().exclude(pk__in=estornados)

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


class TipoMovimentoComissao(models.TextChoices):
    CREDITO = 'CREDITO', 'Crédito (atendimento realizado)'
    ESTORNO = 'ESTORNO', 'Estorno (cancelamento)'
    PAGAMENTO = 'PAGAMENTO', 'Pagamento (baixa)'


class StatusComissao(models.TextChoices):
    """Status DERIVADO do credito (nao e coluna): MovimentoComissao.objects.creditos_com_status()."""
    PENDENTE = 'PENDENTE', 'Pendente'
    PAGA = 'PAGA', 'Paga'
    ESTORNADA = 'ESTORNADA', 'Estornada'


class MovimentoComissao(models.Model):
    """Livro-razao de comissao (docs/specs/padrao-sql.md §2.9 e §4.f — migration 0053).

    CREDITO (atendimento realizado; 1 por atendimento), PAGAMENTO (baixa no
    painel) e ESTORNO (atendimento cancelado) — os dois ultimos referenciam o
    CREDITO (``movimento_origem``) e repetem profissional, atendimento e valor
    (sem baixa parcial). ``valor`` e sempre positivo; o sinal vem do tipo.
    Status e saldo sao DERIVADOS (``creditos_com_status``,
    ``services/comissao``): o saldo a pagar fica NEGATIVO quando uma
    comissao ja paga e estornada (desconta do proximo repasse).

    So-PG (0053): tg_movimento_comissao_validar (origem = CREDITO do mesmo
    profissional/atendimento/valor; nada de PAGAMENTO em credito estornado) e
    tg_movimento_comissao_imutavel. No SQLite o Python cobre (save/delete).
    """

    Tipo = TipoMovimentoComissao
    Status = StatusComissao
    TIPO_CREDITO = TipoMovimentoComissao.CREDITO
    TIPO_ESTORNO = TipoMovimentoComissao.ESTORNO
    TIPO_PAGAMENTO = TipoMovimentoComissao.PAGAMENTO
    TIPO_CHOICES = TipoMovimentoComissao.choices
    # status DERIVADO do credito (nao e coluna): anotado por creditos_com_status()
    STATUS_PENDENTE = StatusComissao.PENDENTE
    STATUS_PAGA = StatusComissao.PAGA
    STATUS_ESTORNADA = StatusComissao.ESTORNADA
    STATUS_CHOICES = StatusComissao.choices

    # FKs sem indice automatico: os ix_ do Meta cobrem (spec §2.9)
    profissional = models.ForeignKey(
        Profissional, on_delete=models.PROTECT, related_name='comissoes', db_index=False,
    )
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.PROTECT, related_name='comissoes', db_index=False,
    )
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default=TIPO_CREDITO)
    # PROTECT: regra ja usada nao se exclui (o SET NULL bateria no trigger) — desative
    regra = models.ForeignKey(
        RegraComissao, on_delete=models.PROTECT, blank=True, null=True, db_column='regra_comissao_id',
        db_index=False,
    )
    valor = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        db_comment='Sempre positivo; o sinal vem do tipo (CREDITO +, ESTORNO/PAGAMENTO -).',
    )
    # Snapshot do calculo no CREDITO (como o item congelado da 0052): a regra pode ser desativada e
    # o preco mudar, e o credito continua explicavel. NULL nos derivados e nos creditos anteriores a 0066.
    # (db_comment sem o simbolo de porcento: o COMMENT ON COLUMN do PG passa pelo mogrify do psycopg)
    base_calculo = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True, db_column='valor_base_calculo',
        db_comment='CREDITO: base em que a regra incidiu (valor cobrado ou unitario do pacote), congelada.',
    )
    percentual_aplicado = models.DecimalField(
        max_digits=5, decimal_places=2, blank=True, null=True,
        db_comment='CREDITO de regra percentual: o percentual aplicado (no maximo 100). NULL em regra de valor fixo.',
    )
    # CREDITO que o ESTORNO/PAGAMENTO liquida. Sem indice automatico: coberto pelos
    # UNIQUE parciais e pelo ix_movimento_comissao_origem_parcial.
    movimento_origem = models.ForeignKey(
        'self', on_delete=models.PROTECT, blank=True, null=True,
        related_name='lancamentos_derivados', db_column='movimento_comissao_origem_id',
        db_index=False,
        db_comment='CREDITO que o ESTORNO/PAGAMENTO liquida (NULL no proprio CREDITO).',
    )
    observacoes = models.TextField(blank=True, default='', db_default='', db_column='observacao')
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
        db_table_comment = (
            'Ledger append-only de comissao: CREDITO (atendimento realizado), ESTORNO (cancelamento) e '
            'PAGAMENTO (baixa), estes dois referenciando o CREDITO. Status e saldo sao derivados (vw_comissao, '
            'vw_comissao_saldo). Imutavel (tg_movimento_comissao_imutavel).'
        )
        constraints = [
            models.CheckConstraint(
                condition=Q(tipo__in=TipoMovimentoComissao.values),
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
            models.CheckConstraint(
                condition=Q(tipo='CREDITO') | (Q(base_calculo__isnull=True) & Q(percentual_aplicado__isnull=True)),
                name='ck_movimento_comissao_snapshot_so_credito',
            ),
            # comissao nunca passa da base (percentual <= 100; valor fixo limitado a base)
            models.CheckConstraint(
                condition=Q(base_calculo__isnull=True) | (Q(base_calculo__gte=0) & Q(valor__lte=models.F('base_calculo'))),
                name='ck_movimento_comissao_valor_ate_base_calculo',
            ),
            models.CheckConstraint(
                condition=Q(percentual_aplicado__isnull=True) | (Q(percentual_aplicado__gte=0)
                                                                 & Q(percentual_aplicado__lte=100)),
                name='ck_movimento_comissao_percentual_aplicado_0_100',
            ),
            # no maximo 1 CREDITO ATIVO (nao estornado) por atendimento: tg_movimento_comissao_validar
            # (0063) + _validar_lancamento — estornado, pode nascer outro (valor corrigido, realizacao refeita)
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
            Indice(fields=['-criado_em'], name='ix_movimento_comissao_criado_em'),
            Indice(fields=['profissional', '-criado_em'], name='ix_movimento_comissao_profissional_criado_em'),
            Indice(fields=['atendimento'], name='ix_movimento_comissao_atendimento'),
            Indice(
                fields=['regra'], condition=Q(regra__isnull=False), name='ix_movimento_comissao_regra_comissao_parcial',
            ),
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
        elif self.atendimento_id and MovimentoComissao.objects.creditos_ativos().filter(
                atendimento_id=self.atendimento_id).exists():
            # espelho do tg_movimento_comissao_validar (0063): estorne o credito ativo antes
            raise IntegrityError('movimento_comissao: atendimento ja tem credito ativo')
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
