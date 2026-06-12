# aranha_estetica/models/clientes.py — Clientes
import secrets
from django.db import models
from django.core.validators import MinValueValidator

from django.db.models.functions import Lower

from aranha_estetica.validators import (
    validate_cpf, validate_telefone_br, validate_data_nascimento,
    normalizar_telefone, normalizar_cpf,
)


class ClienteAtivosManager(models.Manager):
    """Default manager que exclui clientes soft-deleted."""

    def get_queryset(self):
        return super().get_queryset().filter(deletado_em__isnull=True)


class Cliente(models.Model):
    nome = models.CharField(max_length=150)
    data_nascimento = models.DateField(
        blank=True, null=True, validators=[validate_data_nascimento],
    )
    # unique global removido — uniq_cliente_cpf_ativo (parcial) permite
    # re-cadastro pos soft-delete; max 11 (digits-only canonico)
    cpf = models.CharField(
        max_length=11, blank=True, null=True, validators=[validate_cpf],
    )
    rg = models.CharField(max_length=20, blank=True, null=True)
    profissao = models.CharField(max_length=100, blank=True, null=True)
    # email indexado pelo UNIQUE parcial uniq_cliente_email_ativo — sem db_index redundante
    email = models.EmailField(max_length=254, blank=True, null=True)
    telefone = models.CharField(
        max_length=20, blank=True, null=True, validators=[validate_telefone_br],
    )
    cep = models.CharField(max_length=10, blank=True, null=True)
    endereco = models.CharField(max_length=255, blank=True, null=True)
    ativo = models.BooleanField(default=True)
    faltas_consecutivas = models.SmallIntegerField(default=0, validators=[MinValueValidator(0)])
    bloqueado_online = models.BooleanField(default=False)

    # Estetica/CRM
    foto_url = models.URLField(max_length=600, blank=True, default='', help_text='URL S3 / storage')
    indicado_por = models.ForeignKey(
        'self', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='indicacoes',
        help_text='Cliente que indicou este (programa fidelidade)',
    )
    codigo_indicacao = models.CharField(
        max_length=12, unique=True, blank=True, null=True, db_index=True,
        help_text='Codigo curto compartilhavel (ex: A1B2C3D4) gerado automaticamente.',
    )

    # LGPD: consentimento legado (comunicacao transacional) e unsubscribe token
    aceita_comunicacao = models.BooleanField(default=True)
    token_descadastro = models.CharField(max_length=64, blank=True, null=True, db_index=True)

    # LGPD: consents granulares por canal/uso — opt-in explicito com audit trail
    consent_email_marketing = models.BooleanField(default=False)
    consent_email_marketing_em = models.DateTimeField(blank=True, null=True)
    consent_email_marketing_ip = models.GenericIPAddressField(blank=True, null=True)

    consent_whatsapp_nps = models.BooleanField(default=False)
    consent_whatsapp_nps_em = models.DateTimeField(blank=True, null=True)
    consent_whatsapp_nps_ip = models.GenericIPAddressField(blank=True, null=True)

    consent_whatsapp_confirmacao = models.BooleanField(default=False)
    consent_whatsapp_confirmacao_em = models.DateTimeField(blank=True, null=True)
    consent_whatsapp_confirmacao_ip = models.GenericIPAddressField(blank=True, null=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True, null=True)
    deletado_em = models.DateTimeField(blank=True, null=True, db_index=True)

    objects = ClienteAtivosManager()
    all_objects = models.Manager()

    class Meta:
        managed = True
        db_table = 'cliente'
        indexes = [
            models.Index(fields=['telefone'], name='idx_cliente_telefone'),
            models.Index(fields=['nome'], name='idx_cliente_nome'),
            # idx_cliente_email removido — uniq_cliente_email_ativo ja indexa
        ]
        constraints = [
            # UNIQUE parcial case-insensitive: email duplicado proibido entre ativos
            # (Lower() — maria@X.com == MARIA@x.com; null/'' permitidos)
            models.UniqueConstraint(
                Lower('email'),
                condition=models.Q(deletado_em__isnull=True) & ~models.Q(email__isnull=True) & ~models.Q(email=''),
                name='uniq_cliente_email_ativo',
            ),
            # CHAVE NATURAL do booking publico: 1 cliente ativo por telefone
            models.UniqueConstraint(
                fields=['telefone'],
                condition=models.Q(deletado_em__isnull=True) & ~models.Q(telefone__isnull=True) & ~models.Q(telefone=''),
                name='uniq_cliente_telefone_ativo',
            ),
            # CPF unico entre ativos
            models.UniqueConstraint(
                fields=['cpf'],
                condition=models.Q(deletado_em__isnull=True) & ~models.Q(cpf__isnull=True) & ~models.Q(cpf=''),
                name='uniq_cliente_cpf_ativo',
            ),
            models.CheckConstraint(
                check=~models.Q(indicado_por_id=models.F('id')),
                name='chk_cliente_nao_indica_si_mesmo',
            ),
        ]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        # Forma canonica de identidade (remodelagem v2.1 fase 3) — telefone e
        # cpf sempre digits-only; lookups das views comparam normalizado.
        if self.telefone:
            self.telefone = normalizar_telefone(self.telefone)
        if self.cpf:
            self.cpf = normalizar_cpf(self.cpf)
        if not self.token_descadastro:
            self.token_descadastro = secrets.token_urlsafe(32)
        if not self.codigo_indicacao:
            self.codigo_indicacao = self._gerar_codigo_indicacao()
        super().save(*args, **kwargs)

    @staticmethod
    def _gerar_codigo_indicacao() -> str:
        """Gera codigo unico 8 chars hex maiusculo. Tenta 8 vezes — alfabeto 16^8 = 4G colisao improvavel."""
        for _ in range(8):
            candidato = secrets.token_hex(4).upper()
            if not Cliente.all_objects.filter(codigo_indicacao=candidato).exists():
                return candidato
        # fallback: 12 chars caso 8 colisoes (loteria)
        return secrets.token_hex(6).upper()

    def registrar_falta(self):
        self.faltas_consecutivas += 1
        if self.faltas_consecutivas >= 3:
            self.bloqueado_online = True
        self.save()

    def resetar_faltas(self):
        self.faltas_consecutivas = 0
        self.bloqueado_online = False
        self.save()

    def soft_delete(self):
        from django.utils import timezone
        self.deletado_em = timezone.now()
        self.ativo = False
        self.save()
