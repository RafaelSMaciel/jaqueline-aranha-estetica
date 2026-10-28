# aranha_estetica/models/pacotes.py — Pacotes de servicos
"""Pacotes (docs/specs/padrao-sql.md §2.8 e §4.e — migration 0052).

Catalogo (``Pacote`` + ``ItemPacote``) e editavel a qualquer momento: a venda
CONGELA o que foi vendido em ``CompraPacote.pacote_nome`` +
``CompraPacoteItem`` (procedimento, nome, sessoes e valor unitario). Saldo,
debito de sessao, finalizacao e comissao leem os itens congelados — nunca o
catalogo.

So-PG (migration 0052): ``fk_consumo_sessao_compra_pacote_item_compra`` (o item
e da mesma compra), ``tg_consumo_sessao_validar`` (item da compra, mesmo
procedimento, nao-retorno, saldo) e ``tg_compra_pacote_item_imutavel``. O
Python cobre o mesmo no SQLite (``ConsumoSessao.save``/``CompraPacoteItem.save``).
"""
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Count, Q
from django.db.models.functions import Now
from django.utils import timezone

from ..db.indices import Indice
from .clientes import Cliente
from .mixins import atualizado_em_legado, criado_em_legado
from .procedimentos import Procedimento

CENTAVO = Decimal('0.01')
MSG_ITEM_IMUTAVEL = (
    'Item de compra de pacote é o que foi vendido (snapshot da venda): não pode ser alterado.'
)


def rateio_por_sessao(valor_pago, total_sessoes) -> Decimal:
    """Valor de 1 sessao da compra: round(valor_pago / sessoes, 2), meio p/ cima.

    Mesma formula que a comissao de sessao de pacote usava antes da 0052
    (ComissaoService.base_calculo): a comissao nao muda com o congelamento.
    """
    if not total_sessoes or total_sessoes <= 0:
        return Decimal('0.00')
    return (Decimal(valor_pago) / Decimal(total_sessoes)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


class Pacote(models.Model):
    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True, default='', db_default='')
    preco_total = models.DecimalField(max_digits=10, decimal_places=2, db_column='valor_total')
    ativo = models.BooleanField(default=True, db_default=True, db_column='eh_ativo')
    validade_meses = models.SmallIntegerField(default=12, db_default=12)
    criado_em = criado_em_legado()
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'pacote'
        db_table_comment = (
            'Catalogo de pacotes, editavel a qualquer momento: a venda congela nome/itens em '
            'compra_pacote/compra_pacote_item.'
        )
        constraints = [
            models.CheckConstraint(
                condition=models.Q(preco_total__gte=0),
                name='ck_pacote_valor_total_nao_negativo'
            ),
            models.CheckConstraint(
                condition=models.Q(validade_meses__gt=0),
                name='ck_pacote_validade_meses_positiva'
            ),
        ]

    def __str__(self):
        return self.nome


class ItemPacote(models.Model):
    """Item do CATALOGO (editavel): so vale p/ vendas futuras."""
    # FKs sem indice automatico: pacote coberto pelo UNIQUE (prefixo), procedimento pelo ix_
    pacote = models.ForeignKey(Pacote, on_delete=models.CASCADE, related_name='itens', db_index=False)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE, db_index=False)
    quantidade_sessoes = models.SmallIntegerField(default=1, db_default=1, db_column='qtd_sessoes')
    criado_em = criado_em_legado()

    class Meta:
        managed = True
        db_table = 'pacote_item'
        db_table_comment = 'Itens do pacote no catalogo (procedimento x sessoes).'
        constraints = [
            models.UniqueConstraint(fields=['pacote', 'procedimento'], name='uq_pacote_item_pacote_procedimento'),
            models.CheckConstraint(
                condition=models.Q(quantidade_sessoes__gt=0),
                name='ck_pacote_item_qtd_sessoes_positiva'
            ),
        ]
        indexes = [
            Indice(fields=['procedimento'], name='ix_pacote_item_procedimento'),
        ]


class StatusCompraPacote(models.TextChoices):
    ATIVO = 'ATIVO', 'Ativo'
    FINALIZADO = 'FINALIZADO', 'Finalizado'
    CANCELADO = 'CANCELADO', 'Cancelado'
    EXPIRADO = 'EXPIRADO', 'Expirado'


class CompraPacote(models.Model):
    Status = StatusCompraPacote
    STATUS_ATIVO = StatusCompraPacote.ATIVO
    STATUS_FINALIZADO = StatusCompraPacote.FINALIZADO
    STATUS_CANCELADO = StatusCompraPacote.CANCELADO
    STATUS_EXPIRADO = StatusCompraPacote.EXPIRADO
    STATUS_CHOICES = StatusCompraPacote.choices

    # PROTECT: compra e registro fiscal — nao cascateia com o cliente
    # FKs sem indice automatico: cliente coberto pelo ix_compra_pacote_cliente_status, pacote pelo ix_
    cliente = models.ForeignKey(
        Cliente, on_delete=models.PROTECT, related_name='pacotes_comprados', db_index=False,
    )
    pacote = models.ForeignKey(Pacote, on_delete=models.RESTRICT, db_index=False)
    pacote_nome = models.CharField(
        max_length=150,
        db_comment='Nome do pacote no momento da venda (o catalogo pode mudar depois).',
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    atualizado_em = atualizado_em_legado()
    valor_pago = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)],
    )
    # Devolvido ao cliente no cancelamento (receita liquida = pago - reembolsado)
    valor_reembolsado = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, db_default=0, validators=[MinValueValidator(0)],
    )
    status = models.CharField(
        max_length=20, default=StatusCompraPacote.ATIVO, db_default=StatusCompraPacote.ATIVO,
        choices=StatusCompraPacote.choices,
    )
    data_expiracao = models.DateField(
        blank=True, null=True,
        db_comment=(
            'Ultimo dia valido (data local); sessao feita ate esse dia debita mesmo com o job ja tendo '
            'marcado EXPIRADO.'
        ),
    )

    class Meta:
        managed = True
        db_table = 'compra_pacote'
        db_table_comment = (
            'Venda de pacote (registro fiscal, PROTECT). Receita = valor_pago - valor_reembolsado na data '
            'da venda (D20).'
        )
        indexes = [
            Indice(fields=['cliente', 'status'], name='ix_compra_pacote_cliente_status'),
            Indice(fields=['pacote'], name='ix_compra_pacote_pacote'),
            # receita de pacotes por periodo (dashboard, financeiro)
            Indice(fields=['criado_em'], name='ix_compra_pacote_criado_em'),
            # expiracao (job_expirar_pacotes, job_verificar_pacotes_expirando)
            Indice(
                fields=['data_expiracao'], condition=models.Q(status='ATIVO'),
                name='ix_compra_pacote_data_expiracao_ativo_parcial',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=StatusCompraPacote.values),
                name='ck_compra_pacote_status'
            ),
            models.CheckConstraint(
                condition=models.Q(valor_pago__gte=0),
                name='ck_compra_pacote_valor_pago_nao_negativo',
            ),
            # reembolso nunca negativo nem maior que o pago
            models.CheckConstraint(
                condition=models.Q(valor_reembolsado__gte=0)
                & models.Q(valor_reembolsado__lte=models.F('valor_pago')),
                name='ck_compra_pacote_valor_reembolsado',
            ),
        ]

    # ─── venda ──────────────────────────────────────────────────────────
    @classmethod
    def vender(cls, pacote, cliente, valor_pago, **campos) -> 'CompraPacote':
        """Registra a venda congelando nome e itens do catalogo (atomico).

        Cada item vira um CompraPacoteItem com o procedimento, o nome, as sessoes e
        o valor unitario = valor_pago / total de sessoes (rateio_por_sessao) — base
        da comissao da sessao de pacote. Editar o catalogo depois so afeta vendas
        futuras.
        """
        with transaction.atomic():
            compra = cls(
                cliente=cliente, pacote=pacote, pacote_nome=pacote.nome,
                valor_pago=valor_pago, **campos,
            )
            compra.save()
            compra.congelar_itens()
        return compra

    def congelar_itens(self):
        """Copia os itens ATUAIS do catalogo p/ a compra (so na venda; idempotente)."""
        if self.itens.exists():
            return list(self.itens.all())
        do_catalogo = list(self.pacote.itens.select_related('procedimento').order_by('pk'))
        unitario = rateio_por_sessao(self.valor_pago, sum(i.quantidade_sessoes for i in do_catalogo))
        return CompraPacoteItem.objects.bulk_create([
            CompraPacoteItem(
                compra_pacote=self,
                procedimento=item.procedimento,
                procedimento_nome=item.procedimento.nome,
                quantidade_sessoes=item.quantidade_sessoes,
                valor_unitario=unitario,
            )
            for item in do_catalogo
        ])

    def save(self, *args, **kwargs):
        if self._state.adding and not self.pacote_nome and self.pacote_id:
            self.pacote_nome = self.pacote.nome
        if not self.data_expiracao and self.pacote and self.pacote.validade_meses:
            from dateutil.relativedelta import relativedelta
            # localdate() como base evita off-by-one de fuso: timezone.now() e UTC,
            # e somar meses + .date() perto da meia-noite local deslocaria 1 dia.
            self.data_expiracao = (
                timezone.localdate() + relativedelta(months=self.pacote.validade_meses)
            )
        super().save(*args, **kwargs)

    # ─── saldo (itens congelados) ───────────────────────────────────────
    def _prefetch(self, nome):
        return nome in getattr(self, '_prefetched_objects_cache', {})

    def _itens_congelados(self):
        if self._prefetch('itens'):
            return list(self.itens.all())
        return list(self.itens.select_related('procedimento').order_by('pk'))

    def consumos_por_item(self) -> dict:
        """{compra_pacote_item_id: sessoes consumidas} — 1 query agregada (ou o prefetch)."""
        if self._prefetch('sessoes_realizadas'):
            return dict(Counter(c.compra_pacote_item_id for c in self.sessoes_realizadas.all()))
        return dict(
            self.sessoes_realizadas.order_by().values('compra_pacote_item')
            .annotate(c=Count('id')).values_list('compra_pacote_item', 'c')
        )

    def saldo_por_procedimento(self):
        """Sessoes por item CONGELADO: [{item, procedimento, procedimento_nome, total, usadas, restantes}].

        Fonte unica do saldo (ficha do cliente, e-mail de expiracao, cancelamento).
        So ConsumoSessao conta (retorno gratuito nao consome). Em listas,
        prefetch 'itens__procedimento' e 'sessoes_realizadas' evita N+1.
        Espelho SQL: fn_saldo_pacote / vw_pacote_saldo (paridade, fase L2).
        """
        usadas_por_item = self.consumos_por_item()
        saldo = []
        for item in self._itens_congelados():
            usadas = usadas_por_item.get(item.pk, 0)
            saldo.append({
                'item': item,
                'procedimento': item.procedimento,
                'procedimento_nome': item.procedimento_nome,
                'total': item.quantidade_sessoes,
                'usadas': usadas,
                'restantes': max(0, item.quantidade_sessoes - usadas),
            })
        return saldo

    def sessoes_restantes(self):
        """Total de sessoes ainda nao usadas (soma de saldo_por_procedimento)."""
        return sum(linha['restantes'] for linha in self.saldo_por_procedimento())

    def verificar_finalizacao(self):
        # Contagem agregada numa unica query; leitura + finalizacao sob lock
        # para serializar consumos concorrentes.
        with transaction.atomic():
            travada = CompraPacote.objects.select_for_update().get(pk=self.pk)
            usadas = travada.consumos_por_item()
            for item in travada.itens.all():
                if usadas.get(item.pk, 0) < item.quantidade_sessoes:
                    return
            travada.status = self.STATUS_FINALIZADO
            travada.save(update_fields=['status'])
        self.status = travada.status

    def __str__(self):
        cliente_nome = self.cliente.nome if self.cliente_id else 's/ cliente'
        return f'{cliente_nome} — {self.pacote_nome or "s/ pacote"} ({self.get_status_display()})'


class CompraPacoteItemQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError(MSG_ITEM_IMUTAVEL)

    update.alters_data = True
    update.queryset_only = True


class CompraPacoteItem(models.Model):
    """Item vendido (snapshot): procedimento, nome, sessoes e valor de 1 sessao.

    Imutavel (tg_compra_pacote_item_imutavel; no Python, save() de linha
    existente e QuerySet.update() recusam). Sai so junto com a compra (CASCADE).
    """
    # FK sem indice automatico: coberta pelo uq_compra_pacote_item_compra_procedimento
    compra_pacote = models.ForeignKey(
        CompraPacote, on_delete=models.CASCADE, related_name='itens', db_index=False,
    )
    # PROTECT (= NO ACTION): procedimento vendido nao some do registro fiscal
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.PROTECT, related_name='itens_compra_pacote', db_index=False,
    )
    procedimento_nome = models.CharField(
        max_length=100, db_comment='Nome do procedimento no momento da venda.',
    )
    quantidade_sessoes = models.SmallIntegerField(db_column='qtd_sessoes')
    valor_unitario = models.DecimalField(
        max_digits=10, decimal_places=2,
        db_comment=(
            'Rateio do valor pago por sessao: round(valor_pago / total de sessoes da compra, 2) '
            '- base da comissao da sessao de pacote (D20). 0 = item reconstruido pela 0052.'
        ),
    )
    criado_em = models.DateTimeField(default=timezone.now, db_default=Now(), editable=False)

    objects = CompraPacoteItemQuerySet.as_manager()

    class Meta:
        db_table = 'compra_pacote_item'
        db_table_comment = (
            'Itens congelados na venda (snapshot). Saldo = qtd_sessoes - consumos do item '
            '(fn_saldo_pacote). Imutavel (tg_compra_pacote_item_imutavel).'
        )
        ordering = ('id',)
        constraints = [
            models.UniqueConstraint(
                fields=['compra_pacote', 'procedimento'], name='uq_compra_pacote_item_compra_procedimento',
            ),
            # alvo da FK composta fk_consumo_sessao_compra_pacote_item_compra (so PG)
            models.UniqueConstraint(fields=['id', 'compra_pacote'], name='uq_compra_pacote_item_id_compra'),
            models.CheckConstraint(
                condition=Q(quantidade_sessoes__gt=0), name='ck_compra_pacote_item_qtd_sessoes_positiva',
            ),
            models.CheckConstraint(
                condition=Q(valor_unitario__gte=0), name='ck_compra_pacote_item_valor_unitario_nao_negativo',
            ),
        ]
        indexes = [
            Indice(fields=['procedimento'], name='ix_compra_pacote_item_procedimento'),
        ]

    def __str__(self):
        return f'{self.procedimento_nome} ({self.quantidade_sessoes}x)'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(MSG_ITEM_IMUTAVEL)
        super().save(*args, **kwargs)


class ConsumoSessao(models.Model):
    """1 sessao de pacote consumida por atendimento REALIZADO (nunca retorno)."""
    # FK sem indice automatico: coberta pelo ix_consumo_sessao_compra_pacote
    compra_pacote = models.ForeignKey(
        CompraPacote, on_delete=models.CASCADE, related_name='sessoes_realizadas', db_index=False,
    )
    # RESTRICT (= NO ACTION): o item nao some com consumo; apagar a COMPRA leva os
    # dois juntos (o RESTRICT do Django aceita o que tambem sai por CASCADE).
    compra_pacote_item = models.ForeignKey(
        CompraPacoteItem, on_delete=models.RESTRICT, related_name='consumos', db_index=False,
    )
    # OneToOne (NOT NULL) = UNIQUE: 1 atendimento consome no maximo 1 sessao
    atendimento = models.OneToOneField(
        'Atendimento', on_delete=models.RESTRICT, related_name='sessao_pacote_vinculada'
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())

    class Meta:
        managed = True
        db_table = 'consumo_sessao'
        db_table_comment = (
            '1 sessao de pacote consumida por atendimento REALIZADO (nunca retorno). tg_consumo_sessao_validar '
            'confere item da compra, procedimento, retorno e saldo.'
        )
        indexes = [
            Indice(fields=['compra_pacote'], name='ix_consumo_sessao_compra_pacote'),
            Indice(fields=['compra_pacote_item'], name='ix_consumo_sessao_compra_pacote_item'),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.compra_pacote_item_id is None:
                self.compra_pacote_item = self._item_do_atendimento()
            self._validar()
        super().save(*args, **kwargs)

    def _item_do_atendimento(self):
        from .agendamentos import Atendimento
        procedimento_id = (
            Atendimento.objects.filter(pk=self.atendimento_id).values_list('procedimento_id', flat=True).first()
        )
        item = CompraPacoteItem.objects.filter(
            compra_pacote_id=self.compra_pacote_id, procedimento_id=procedimento_id,
        ).first()
        if item is None:
            raise ValidationError('A compra não tem item deste procedimento: não há sessão a consumir.')
        return item

    def _validar(self):
        """Espelho Python de tg_consumo_sessao_validar (o banco garante no PG)."""
        from .agendamentos import Atendimento
        item = CompraPacoteItem.objects.filter(pk=self.compra_pacote_item_id).values(
            'compra_pacote_id', 'procedimento_id', 'quantidade_sessoes',
        ).first()
        atend = Atendimento.objects.filter(pk=self.atendimento_id).values('procedimento_id', 'eh_retorno').first()
        if item is None or atend is None:
            return  # a FK acusa
        if item['compra_pacote_id'] != self.compra_pacote_id:
            raise ValidationError('O item consumido não pertence a esta compra de pacote.')
        if atend['eh_retorno']:
            raise ValidationError('Retorno gratuito não consome sessão de pacote.')
        if atend['procedimento_id'] != item['procedimento_id']:
            raise ValidationError('O procedimento do atendimento é diferente do item do pacote.')
        usadas = ConsumoSessao.objects.filter(compra_pacote_item_id=self.compra_pacote_item_id).count()
        if usadas >= item['quantidade_sessoes']:
            raise ValidationError('Este item do pacote não tem mais sessões disponíveis.')
