# app_shivazen/models/procedimentos.py — Procedimentos, precos, promocoes
from datetime import date

from django.db import models

from .profissionais import Profissional


class Procedimento(models.Model):
    CATEGORIA_CHOICES = [
        ('FACIAL', 'Facial'),
        ('CORPORAL', 'Corporal'),
        ('CAPILAR', 'Capilar'),
        ('OUTRO', 'Outro'),
    ]

    nome = models.CharField(max_length=100)
    slug = models.SlugField(max_length=140, unique=True, blank=True, null=True)
    descricao = models.TextField(blank=True, null=True)
    descricao_longa = models.TextField(blank=True, null=True)
    duracao_minutos = models.SmallIntegerField()
    categoria = models.CharField(max_length=20, default='OUTRO', choices=CATEGORIA_CHOICES)
    imagem_destaque = models.URLField(max_length=500, blank=True, null=True)
    ativo = models.BooleanField(default=True)
    profissionais = models.ManyToManyField(Profissional, through='ProfissionalProcedimento')

    class Meta:
        managed = True
        db_table = 'procedimento'
        indexes = [
            models.Index(fields=['ativo'], name='idx_procedimento_ativo'),
            models.Index(fields=['categoria'], name='idx_procedimento_categoria'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(duracao_minutos__gt=0),
                name='chk_procedimento_duracao_positiva'
            ),
            models.CheckConstraint(
                check=models.Q(categoria__in=['FACIAL', 'CORPORAL', 'CAPILAR', 'OUTRO']),
                name='chk_procedimento_categoria'
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


class ProfissionalProcedimento(models.Model):
    profissional = models.ForeignKey(Profissional, on_delete=models.CASCADE)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE)

    class Meta:
        managed = True
        db_table = 'profissional_procedimento'
        unique_together = (('profissional', 'procedimento'),)


class Preco(models.Model):
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE)
    profissional = models.ForeignKey(
        Profissional, on_delete=models.CASCADE, blank=True, null=True
    )
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    descricao = models.TextField(blank=True, null=True)
    vigente_desde = models.DateField(default=date.today)

    class Meta:
        managed = True
        db_table = 'preco'
        indexes = [
            models.Index(fields=['procedimento', 'profissional', '-vigente_desde'], name='idx_preco_lookup'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(valor__gte=0),
                name='chk_preco_valor_positivo'
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
        indexes = [
            models.Index(fields=['ativa', 'data_inicio', 'data_fim'], name='idx_promocao_vigente'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(data_fim__gte=models.F('data_inicio')),
                name='chk_promocao_data_fim_valida'
            ),
        ]

    @property
    def esta_vigente(self):
        from django.utils import timezone
        hoje = timezone.now().date()
        return self.ativa and self.data_inicio <= hoje <= self.data_fim

    def __str__(self):
        return self.nome
