# app_shivazen/models/clientes.py — Clientes
import secrets
from django.db import models
from django.core.validators import MinValueValidator

from app_shivazen.validators import (
    validate_cpf, validate_telefone_br, validate_data_nascimento,
)


class ClienteAtivosManager(models.Manager):
    """Default manager que exclui clientes soft-deleted."""

    def get_queryset(self):
        return super().get_queryset().filter(deletado_em__isnull=True)


class Cliente(models.Model):
    nome_completo = models.CharField(max_length=150)
    data_nascimento = models.DateField(
        blank=True, null=True, validators=[validate_data_nascimento],
    )
    cpf = models.CharField(
        max_length=14, unique=True, blank=True, null=True, validators=[validate_cpf],
    )
    rg = models.CharField(max_length=20, blank=True, null=True)
    profissao = models.TextField(blank=True, null=True)
    email = models.EmailField(max_length=100, blank=True, null=True)
    telefone = models.CharField(
        max_length=20, blank=True, null=True, validators=[validate_telefone_br],
    )
    cep = models.CharField(max_length=10, blank=True, null=True)
    endereco = models.TextField(blank=True, null=True)
    ativo = models.BooleanField(default=True)
    faltas_consecutivas = models.SmallIntegerField(default=0, validators=[MinValueValidator(0)])
    bloqueado_online = models.BooleanField(default=False)

    # LGPD: consentimento de comunicacao e unsubscribe via token publico
    aceita_comunicacao = models.BooleanField(default=True)
    unsubscribe_token = models.CharField(max_length=64, blank=True, null=True, db_index=True)

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
            models.Index(fields=['nome_completo'], name='idx_cliente_nome'),
        ]

    def __str__(self):
        return self.nome_completo

    def save(self, *args, **kwargs):
        if not self.unsubscribe_token:
            self.unsubscribe_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

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
