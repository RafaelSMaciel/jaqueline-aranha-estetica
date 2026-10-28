# aranha_estetica/models/procedimentos.py — Procedimentos, precos, promocoes
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from ..db.indices import Indice
from .mixins import atualizado_em_legado, criado_em_legado
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

    # COLLATE pt_br (ICU no PG; shim em db/collation.py no SQLite) — no estado desde a 0054
    nome = models.CharField(max_length=100, db_collation='pt_br')
    # UNIQUE no Meta (uq_procedimento_slug): sem o indice/_like do SlugField
    slug = models.SlugField(max_length=140, blank=True, null=True, db_index=False)
    descricao = models.TextField(blank=True, default='', db_default='')
    descricao_longa = models.TextField(blank=True, default='', db_default='')
    duracao_minutos = models.SmallIntegerField()
    buffer_minutos = models.SmallIntegerField(
        default=0, db_default=0, db_column='intervalo_minutos',
        db_comment='Folga apos o procedimento (limpeza/preparo) que tambem ocupa a agenda (0-120).',
    )
    categoria = models.CharField(max_length=20, default='OUTRO', db_default='OUTRO', choices=CATEGORIA_CHOICES)
    modalidade = models.CharField(
        max_length=12, default='PRESENCIAL', db_default='PRESENCIAL', choices=MODALIDADE_CHOICES,
    )
    link_videoconferencia = models.URLField(
        max_length=500, blank=True, default='', db_default='', db_column='url_videoconferencia',
    )
    imagem_destaque = models.URLField(
        max_length=500, blank=True, default='', db_default='', db_column='url_imagem_destaque',
    )
    ativo = models.BooleanField(default=True, db_default=True, db_column='eh_ativo')
    profissionais = models.ManyToManyField(Profissional, through='Habilitacao')

    # F-RET — configuracao de retorno obrigatorio
    exige_retorno = models.BooleanField(default=False, db_default=False, db_column='tem_retorno_obrigatorio')
    # SmallIntegerField + CHECK ck_procedimento_*_nao_negativo (ex-PositiveSmallIntegerField, 0054/0055)
    retorno_minimo_dias = models.SmallIntegerField(blank=True, null=True, validators=[MinValueValidator(0)])
    retorno_maximo_dias = models.SmallIntegerField(blank=True, null=True, validators=[MinValueValidator(0)])
    duracao_retorno_minutos = models.SmallIntegerField(
        default=30, db_default=30, validators=[MinValueValidator(0)],
        db_comment='0 = usa a duracao padrao do retorno (30 min).',
    )
    criado_em = criado_em_legado()
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'procedimento'
        db_table_comment = (
            'Catalogo de servicos. tem_retorno_obrigatorio dispara o retorno gratuito (RetornoService).'
        )
        constraints = [
            models.UniqueConstraint(fields=['slug'], name='uq_procedimento_slug'),
            models.CheckConstraint(
                condition=models.Q(duracao_minutos__gt=0),
                name='ck_procedimento_duracao_positiva'
            ),
            models.CheckConstraint(
                condition=models.Q(buffer_minutos__gte=0) & models.Q(buffer_minutos__lte=120),
                name='ck_procedimento_intervalo_0_120'
            ),
            models.CheckConstraint(
                condition=models.Q(categoria__in=['FACIAL', 'CORPORAL', 'CAPILAR', 'OUTRO']),
                name='ck_procedimento_categoria'
            ),
            models.CheckConstraint(
                condition=models.Q(modalidade__in=['PRESENCIAL', 'ONLINE', 'HIBRIDO']),
                name='ck_procedimento_modalidade'
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(exige_retorno=False) |
                    models.Q(
                        retorno_minimo_dias__isnull=False,
                        retorno_maximo_dias__isnull=False,
                    )
                ),
                name='ck_procedimento_retorno_coerente',
            ),
            # substituem os CHECK automaticos <t>_<col>_check do PositiveSmallIntegerField (0054)
            models.CheckConstraint(
                condition=models.Q(retorno_minimo_dias__gte=0),
                name='ck_procedimento_retorno_minimo_dias_nao_negativo',
            ),
            models.CheckConstraint(
                condition=models.Q(retorno_maximo_dias__gte=0),
                name='ck_procedimento_retorno_maximo_dias_nao_negativo',
            ),
            models.CheckConstraint(
                condition=models.Q(duracao_retorno_minutos__gte=0),
                name='ck_procedimento_duracao_retorno_nao_negativa',
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
    # FKs sem indice automatico: profissional coberto pelo UNIQUE, procedimento pelo ix_ (spec §2.3)
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE, db_index=False)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE, db_index=False)
    criado_em = criado_em_legado()

    class Meta:
        managed = True
        db_table = 'habilitacao'
        db_table_comment = (
            'Profissional x procedimento que ele realiza. So atende quem tem linha aqui: procedimento sem '
            'habilitacao nao aparece no agendamento (services/disponibilidade.profissionais_para).'
        )
        constraints = [
            models.UniqueConstraint(
                fields=['profissional', 'procedimento'], name='uq_habilitacao_profissional_procedimento',
            ),
        ]
        indexes = [
            Indice(fields=['procedimento'], name='ix_habilitacao_procedimento'),
        ]


class Preco(models.Model):
    # FKs sem indice automatico: procedimento coberto pelo uq_preco_vigencia (prefixo)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE, db_index=False)
    profissional = models.ForeignKey(
        Profissional, on_delete=models.CASCADE, blank=True, null=True, db_index=False,
    )
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    descricao = models.TextField(blank=True, default='', db_default='')
    # localdate: date.today usa o relogio do container (UTC) — apos 21h BRT
    # gravaria a vigencia de amanha
    vigente_desde = models.DateField(default=timezone.localdate, db_column='data_inicio_vigencia')
    criado_em = criado_em_legado()
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'preco'
        db_table_comment = (
            'Preco versionado por data_inicio_vigencia: base (profissional NULL) ou por profissional. Vale a '
            'vigencia mais recente <= data do atendimento (utils/precos).'
        )
        indexes = [
            Indice(
                fields=['profissional'], condition=models.Q(profissional__isnull=False),
                name='ix_preco_profissional_parcial',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valor__gte=0),
                name='ck_preco_valor_nao_negativo'
            ),
            # 1 preco por vigencia: par com profissional e, separado, preco-base (NULL)
            models.UniqueConstraint(
                fields=['procedimento', 'profissional', 'vigente_desde'],
                name='uq_preco_vigencia',
            ),
            models.UniqueConstraint(
                fields=['procedimento', 'vigente_desde'],
                condition=models.Q(profissional__isnull=True),
                name='uq_preco_base_vigencia',
            ),
        ]


class Promocao(models.Model):
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.CASCADE, blank=True, null=True, db_index=False,
    )
    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True, default='', db_default='')
    desconto_percentual = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, db_default=0, db_column='percentual_desconto',
    )
    preco_promocional = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True, db_column='valor_promocional',
    )
    data_inicio = models.DateField()
    data_fim = models.DateField()
    ativa = models.BooleanField(default=True, db_default=True, db_column='eh_ativo')
    imagem_url = models.TextField(
        blank=True, default='', db_default='', db_column='url_imagem',
        db_comment='text (legado sem limite de tamanho): excecao ao varchar das url_*.',
    )
    # Disparo por e-mail em lotes (services/marketing): o cursor anda ANTES de cada lote sair
    envio_cursor = models.BigIntegerField(
        blank=True, null=True,
        db_comment='Disparo por e-mail em andamento: ultimo cliente.id ja reservado p/ envio '
                   '(NULL = nao iniciado ou concluido).',
    )
    disparada_em = models.DateTimeField(
        blank=True, null=True,
        db_comment='Inicio do ultimo disparo por e-mail (NULL = nunca disparada). Novo disparo so com '
                   'confirmacao de reenvio.',
    )
    criado_em = criado_em_legado()
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'promocao'
        db_table_comment = (
            'Desconto percentual XOR preco fixo num periodo. procedimento NULL = promocao geral (so percentual).'
        )
        indexes = [
            Indice(
                fields=['procedimento'], condition=models.Q(procedimento__isnull=False),
                name='ix_promocao_procedimento_parcial',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(data_fim__gte=models.F('data_inicio')),
                name='ck_promocao_periodo'
            ),
            models.CheckConstraint(
                condition=models.Q(desconto_percentual__gte=0) & models.Q(desconto_percentual__lte=100),
                name='ck_promocao_percentual_desconto_0_100',
            ),
            models.CheckConstraint(
                condition=models.Q(preco_promocional__isnull=True) | models.Q(preco_promocional__gte=0),
                name='ck_promocao_valor_promocional_nao_negativo',
            ),
            # XOR: desconto OU preco fixo, nunca ambos (ambiguidade de qual vale)
            models.CheckConstraint(
                condition=~(models.Q(desconto_percentual__gt=0) & models.Q(preco_promocional__isnull=False)),
                name='ck_promocao_desconto_xor_valor',
            ),
            # Promocao geral (sem procedimento) so por percentual: preco fixo
            # geral viraria teto p/ todo o catalogo (utils.precos a ignora)
            models.CheckConstraint(
                condition=models.Q(procedimento__isnull=False) | models.Q(preco_promocional__isnull=True),
                name='ck_promocao_geral_so_percentual',
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
        # que ja e garantido por ck_promocao_desconto_xor_valor no banco).
        hoje = timezone.localdate()
        return self.ativa and self.data_inicio <= hoje <= self.data_fim

    @property
    def envio_em_andamento(self) -> bool:
        return self.envio_cursor is not None

    @property
    def envio_concluido(self) -> bool:
        return self.disparada_em is not None and self.envio_cursor is None

    def __str__(self):
        return self.nome
