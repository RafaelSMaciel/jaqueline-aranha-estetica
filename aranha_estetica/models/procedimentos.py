# aranha_estetica/models/procedimentos.py — Procedimentos, precos, promocoes
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .profissionais import Profissional


class Procedimento(models.Model):
    CATEGORIA_CHOICES = [
        ('FACIAL', 'Facial'),
        ('CORPORAL', 'Corporal'),
        ('CAPILAR', 'Capilar'),
        ('OUTRO', 'Outro'),
    ]

    MODALIDADE_CHOICES = [
        ('PRESENCIAL', 'Presencial'),
        ('ONLINE', 'Online'),
        ('HIBRIDO', 'Híbrido (presencial + online)'),
    ]

    nome = models.CharField(max_length=100)
    slug = models.SlugField(max_length=140, unique=True, blank=True, null=True)
    descricao = models.TextField(blank=True, null=True)
    descricao_longa = models.TextField(blank=True, null=True)
    duracao_minutos = models.SmallIntegerField()
    buffer_minutos = models.SmallIntegerField(default=0)
    categoria = models.CharField(max_length=20, default='OUTRO', choices=CATEGORIA_CHOICES)
    modalidade = models.CharField(
        max_length=12, default='PRESENCIAL', choices=MODALIDADE_CHOICES, db_index=True,
    )
    link_videoconferencia = models.URLField(max_length=500, blank=True, default='')
    imagem_destaque = models.URLField(max_length=500, blank=True, null=True)
    ativo = models.BooleanField(default=True)
    profissionais = models.ManyToManyField(Profissional, through='Habilitacao')

    # F-RET — configuracao de retorno obrigatorio
    exige_retorno = models.BooleanField(default=False)
    retorno_minimo_dias = models.PositiveSmallIntegerField(blank=True, null=True)
    retorno_maximo_dias = models.PositiveSmallIntegerField(blank=True, null=True)
    duracao_retorno_minutos = models.PositiveSmallIntegerField(default=30)

    class Meta:
        managed = True
        db_table = 'procedimento'
        db_table_comment = 'Catalogo de servicos. exige_retorno dispara o retorno gratuito automatico (F-RET).'
        indexes = [
            models.Index(fields=['ativo'], name='idx_procedimento_ativo'),
            models.Index(fields=['categoria'], name='idx_procedimento_categoria'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duracao_minutos__gt=0),
                name='chk_procedimento_duracao_positiva'
            ),
            models.CheckConstraint(
                condition=models.Q(buffer_minutos__gte=0) & models.Q(buffer_minutos__lte=120),
                name='chk_procedimento_buffer_range'
            ),
            models.CheckConstraint(
                condition=models.Q(categoria__in=['FACIAL', 'CORPORAL', 'CAPILAR', 'OUTRO']),
                name='chk_procedimento_categoria'
            ),
            models.CheckConstraint(
                condition=models.Q(modalidade__in=['PRESENCIAL', 'ONLINE', 'HIBRIDO']),
                name='chk_procedimento_modalidade'
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(exige_retorno=False) |
                    models.Q(
                        retorno_minimo_dias__isnull=False,
                        retorno_maximo_dias__isnull=False,
                    )
                ),
                name='chk_procedimento_prazo_retorno_coerente',
            ),
        ]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            base = slugify(self.nome) or f'procedimento-{self.pk or "novo"}'
            slug = base
            counter = 1
            while Procedimento.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                counter += 1
                slug = f'{base}-{counter}'
            self.slug = slug
        super().save(*args, **kwargs)


class Habilitacao(models.Model):
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE, db_index=False)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE)

    class Meta:
        managed = True
        db_table = 'habilitacao'
        db_table_comment = 'Procedimentos que cada profissional esta habilitado a realizar.'
        constraints = [
            models.UniqueConstraint(
                fields=['profissional', 'procedimento'],
                name='uniq_habilitacao_profissional_procedimento',
            ),
        ]


class Preco(models.Model):
    # db_index=False: prefixo de uniq_preco_vigencia
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE, db_index=False)
    profissional = models.ForeignKey(
        Profissional, on_delete=models.CASCADE, blank=True, null=True
    )
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    descricao = models.TextField(blank=True, null=True)
    # localdate: date.today usa o relogio do container (UTC) — apos 21h BRT
    # gravaria a vigencia de amanha
    vigente_desde = models.DateField(default=timezone.localdate)

    class Meta:
        managed = True
        db_table = 'preco'
        db_table_comment = 'Preco por procedimento com vigencia; profissional nulo = preco-base.'
        # idx_preco_lookup removido: uniq_preco_vigencia (mesmas colunas) serve
        # o ORDER BY -vigente_desde por varredura reversa do indice
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valor__gte=0),
                name='chk_preco_valor_positivo'
            ),
            # 1 preco por vigencia: par com profissional e, separado, preco-base (NULL)
            models.UniqueConstraint(
                fields=['procedimento', 'profissional', 'vigente_desde'],
                name='uniq_preco_vigencia',
            ),
            models.UniqueConstraint(
                fields=['procedimento', 'vigente_desde'],
                condition=models.Q(profissional__isnull=True),
                name='uniq_preco_base_vigencia',
            ),
        ]


class Promocao(models.Model):
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.CASCADE, blank=True, null=True
    )
    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True, null=True)
    desconto_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    preco_promocional = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    data_inicio = models.DateField()
    data_fim = models.DateField()
    ativa = models.BooleanField(default=True)
    imagem_url = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'promocao'
        db_table_comment = 'Promocao por percentual ou preco fixo, com periodo de vigencia; procedimento nulo = geral.'
        indexes = [
            models.Index(fields=['ativa', 'data_inicio', 'data_fim'], name='idx_promocao_vigente'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(data_fim__gte=models.F('data_inicio')),
                name='chk_promocao_data_fim_valida'
            ),
            models.CheckConstraint(
                condition=models.Q(desconto_percentual__gte=0) & models.Q(desconto_percentual__lte=100),
                name='chk_promocao_desconto_0_100',
            ),
            models.CheckConstraint(
                condition=models.Q(preco_promocional__isnull=True) | models.Q(preco_promocional__gte=0),
                name='chk_promocao_preco_positivo',
            ),
            # XOR: desconto OU preco fixo, nunca ambos (ambiguidade de qual vale)
            models.CheckConstraint(
                condition=~(models.Q(desconto_percentual__gt=0) & models.Q(preco_promocional__isnull=False)),
                name='chk_promocao_desconto_xor_preco',
            ),
            # Promocao geral (sem procedimento) so por percentual: preco fixo
            # geral viraria teto p/ todo o catalogo (utils.precos a ignora)
            models.CheckConstraint(
                condition=models.Q(procedimento__isnull=False) | models.Q(preco_promocional__isnull=True),
                name='chk_promocao_geral_so_percentual',
            ),
        ]

    def clean(self):
        super().clean()
        if self.procedimento_id is None and self.preco_promocional is not None:
            raise ValidationError({
                'preco_promocional': 'Promoção geral (todos os procedimentos) só pode ter '
                                     'desconto percentual. Para preço fixo, escolha o procedimento.',
            })

    @property
    def esta_vigente(self):
        # Vigencia = janela de datas + flag ativa (nao avalia o XOR desconto/preco,
        # que ja e garantido por chk_promocao_desconto_xor_preco no banco).
        hoje = timezone.localdate()
        return self.ativa and self.data_inicio <= hoje <= self.data_fim

    def __str__(self):
        return self.nome
