"""Models extras pos-MVP: clinico (patch test, foto antes/depois), estoque,
tags, plano de tratamento personalizado, credito cliente.

Padrao Pabau / Aesthetic Record para clinica estetica.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from .mixins import SoftDeleteMixin, TimestampedMixin
from .clientes import Cliente
from .procedimentos import Procedimento
from .profissionais import Profissional


# ═══════════════════════════════════════
#  CLINICO: PATCH TEST + FOTO ANTES/DEPOIS
# ═══════════════════════════════════════

class PatchTest(TimestampedMixin):
    """Teste alergico/contato antes de procedimento sensivel.

    Padrao estetica: aplicar substancia 24-48h antes p/ checar reacao.
    """
    RESULTADO_CHOICES = [
        ('PENDENTE', 'Pendente avaliacao'),
        ('NEGATIVO', 'Negativo (sem reacao)'),
        ('POSITIVO', 'Positivo (reacao)'),
        ('INCONCLUSIVO', 'Inconclusivo'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='patch_tests')
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.SET_NULL, blank=True, null=True,
        help_text='Procedimento que motivou o teste',
    )
    profissional = models.ForeignKey(
        Profissional, on_delete=models.SET_NULL, blank=True, null=True,
    )
    substancia = models.CharField(max_length=200, help_text='Ex: acido hialuronico XYZ, anestesico topico')
    aplicado_em = models.DateTimeField()
    avaliado_em = models.DateTimeField(blank=True, null=True)
    resultado = models.CharField(max_length=20, default='PENDENTE', choices=RESULTADO_CHOICES)
    observacoes = models.TextField(blank=True, default='')

    class Meta:
        managed = True
        db_table = 'patch_test'
        indexes = [
            models.Index(fields=['cliente', '-aplicado_em'], name='idx_patch_cli_data'),
            models.Index(fields=['resultado'], name='idx_patch_resultado'),
        ]

    def __str__(self):
        return f'PatchTest {self.substancia} cli={self.cliente_id} ({self.resultado})'


class FotoAntesDepois(TimestampedMixin):
    """Foto antes/depois vinculada a atendimento ou plano.

    Consent obrigatorio (LGPD + uso de imagem).
    URL aponta p/ S3/storage externo (nao salva binario no DB).
    """
    TIPO_CHOICES = [
        ('ANTES', 'Antes'),
        ('DURANTE', 'Durante'),
        ('DEPOIS', 'Depois'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='fotos')
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='fotos_clinicas',
    )
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.SET_NULL, blank=True, null=True,
    )
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    url = models.URLField(max_length=600, help_text='URL S3 / Cloudinary / storage externo')
    thumbnail_url = models.URLField(max_length=600, blank=True, default='')
    descricao = models.TextField(blank=True, default='')
    capturada_em = models.DateTimeField()
    consent_uso_imagem = models.BooleanField(
        default=False,
        help_text='Cliente autorizou uso (marketing/portfolio). LGPD obrigatorio.',
    )
    consent_uso_imagem_em = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'foto_antes_depois'
        indexes = [
            models.Index(fields=['cliente', '-capturada_em'], name='idx_foto_cli_data'),
            models.Index(fields=['atendimento'], name='idx_foto_atend'),
            models.Index(fields=['tipo'], name='idx_foto_tipo'),
        ]

    def __str__(self):
        return f'Foto {self.tipo} cli={self.cliente_id} {self.capturada_em}'


# ═══════════════════════════════════════
#  ESTOQUE
# ═══════════════════════════════════════

class Produto(TimestampedMixin):
    """Produto/insumo da clinica (acidos, ampolas, descartaveis)."""
    UNIDADE_CHOICES = [
        ('UN', 'Unidade'),
        ('ML', 'Mililitro'),
        ('G', 'Grama'),
        ('SESSAO', 'Sessao'),
    ]

    nome = models.CharField(max_length=150)
    sku = models.CharField(max_length=80, blank=True, default='', db_index=True)
    descricao = models.TextField(blank=True, default='')
    unidade = models.CharField(max_length=10, default='UN', choices=UNIDADE_CHOICES)
    estoque_atual = models.DecimalField(
        max_digits=12, decimal_places=3, default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    estoque_minimo = models.DecimalField(
        max_digits=12, decimal_places=3, default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text='Alerta quando estoque atual <= minimo',
    )
    custo_unitario = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    fornecedor = models.CharField(max_length=200, blank=True, default='')
    validade_proxima = models.DateField(blank=True, null=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'produto'
        indexes = [
            models.Index(fields=['ativo', 'nome'], name='idx_produto_ativo_nome'),
        ]

    def __str__(self):
        return f'{self.nome} ({self.estoque_atual} {self.unidade})'

    @property
    def estoque_baixo(self):
        return self.estoque_atual <= self.estoque_minimo


class MovimentoEstoque(models.Model):
    """Movimentacao entrada/saida (compra, consumo, ajuste, perda)."""
    TIPO_CHOICES = [
        ('ENTRADA', 'Entrada (compra)'),
        ('SAIDA', 'Saida (consumo atendimento)'),
        ('AJUSTE', 'Ajuste manual'),
        ('PERDA', 'Perda/vencimento'),
    ]

    produto = models.ForeignKey(Produto, on_delete=models.RESTRICT, related_name='movimentos')
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='produtos_consumidos',
    )
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    custo_total = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    motivo = models.TextField(blank=True, default='')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'movimento_estoque'
        indexes = [
            models.Index(fields=['produto', '-criado_em'], name='idx_movest_prod_data'),
            models.Index(fields=['atendimento'], name='idx_movest_atend'),
            models.Index(fields=['tipo'], name='idx_movest_tipo'),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} {self.quantidade} {self.produto.nome}'


# ═══════════════════════════════════════
#  TAGS / SEGMENTACAO
# ═══════════════════════════════════════

class Tag(models.Model):
    """Tag generica p/ segmentar clientes (VIP, gestante, alergico, idoso)."""
    nome = models.CharField(max_length=60, unique=True)
    cor = models.CharField(max_length=20, default='#C9A84C', help_text='Hex color')
    descricao = models.CharField(max_length=255, blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'tag'

    def __str__(self):
        return self.nome


class ClienteTag(models.Model):
    """M2M Cliente <-> Tag c/ contexto."""
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='cliente_tags')
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    aplicada_em = models.DateTimeField(auto_now_add=True)
    aplicada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True,
    )

    class Meta:
        managed = True
        db_table = 'cliente_tag'
        constraints = [
            models.UniqueConstraint(fields=['cliente', 'tag'], name='uniq_cliente_tag'),
        ]
        indexes = [
            models.Index(fields=['tag'], name='idx_cliente_tag_tag'),
        ]


# ═══════════════════════════════════════
#  PLANO DE TRATAMENTO PERSONALIZADO
# ═══════════════════════════════════════

class PlanoTratamento(TimestampedMixin):
    """Plano de tratamento custom por cliente (multi-procedimento c/ progresso).

    Diferente de Pacote (que e catalogo). Plano e individualizado.
    """
    STATUS_CHOICES = [
        ('ATIVO', 'Ativo'),
        ('CONCLUIDO', 'Concluido'),
        ('PAUSADO', 'Pausado'),
        ('CANCELADO', 'Cancelado'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='planos')
    nome = models.CharField(max_length=150, help_text='Ex: "Plano facial Maria 2026"')
    objetivo = models.TextField(blank=True, default='', help_text='Resultado esperado')
    profissional_responsavel = models.ForeignKey(
        Profissional, on_delete=models.SET_NULL, blank=True, null=True,
    )
    data_inicio = models.DateField()
    data_fim_prevista = models.DateField(blank=True, null=True)
    valor_total = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    desconto = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    status = models.CharField(max_length=20, default='ATIVO', choices=STATUS_CHOICES)
    observacoes = models.TextField(blank=True, default='')

    class Meta:
        managed = True
        db_table = 'plano_tratamento'
        indexes = [
            models.Index(fields=['cliente', '-criado_em'], name='idx_plano_cli'),
            models.Index(fields=['status'], name='idx_plano_status'),
        ]

    def __str__(self):
        return f'{self.nome} ({self.cliente.nome_completo})'


class ItemPlanoTratamento(models.Model):
    """Procedimento dentro do plano c/ qtd de sessoes prevista."""
    plano = models.ForeignKey(PlanoTratamento, on_delete=models.CASCADE, related_name='itens')
    procedimento = models.ForeignKey(Procedimento, on_delete=models.RESTRICT)
    sessoes_previstas = models.SmallIntegerField(default=1, validators=[MinValueValidator(1)])
    sessoes_realizadas = models.SmallIntegerField(default=0)
    observacoes = models.TextField(blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'item_plano_tratamento'
        indexes = [
            models.Index(fields=['plano'], name='idx_itemplano_plano'),
        ]

    def __str__(self):
        return f'{self.procedimento.nome} {self.sessoes_realizadas}/{self.sessoes_previstas}'

    @property
    def progresso_pct(self):
        if not self.sessoes_previstas:
            return 0
        return round(100 * self.sessoes_realizadas / self.sessoes_previstas, 1)


# ═══════════════════════════════════════
#  CREDITO CLIENTE (saldo)
# ═══════════════════════════════════════

class CreditoCliente(TimestampedMixin):
    """Saldo do cliente (vale presente, credito de cancelamento, refund)."""
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE, related_name='credito')
    saldo = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
    )

    class Meta:
        managed = True
        db_table = 'credito_cliente'

    def __str__(self):
        return f'Credito {self.cliente.nome_completo}: R$ {self.saldo}'


class MovimentoCredito(models.Model):
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
        ('OUTRO', 'Outro'),
    ]

    credito = models.ForeignKey(CreditoCliente, on_delete=models.CASCADE, related_name='movimentos')
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    origem = models.CharField(max_length=30, choices=ORIGEM_CHOICES, default='OUTRO')
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    saldo_apos = models.DecimalField(max_digits=10, decimal_places=2)
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
        db_table = 'movimento_credito'
        indexes = [
            models.Index(fields=['credito', '-criado_em'], name='idx_movcred_cred'),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} R$ {self.valor} ({self.origem})'
