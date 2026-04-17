# app_shivazen/models/clientes.py — Clientes
from django.db import models


class Cliente(models.Model):
    nome_completo = models.CharField(max_length=150)
    data_nascimento = models.DateField(blank=True, null=True)
    cpf = models.CharField(max_length=14, unique=True, blank=True, null=True)
    rg = models.CharField(max_length=20, blank=True, null=True)
    profissao = models.TextField(blank=True, null=True)
    email = models.EmailField(max_length=100, blank=True, null=True)
    telefone = models.CharField(max_length=20, blank=True, null=True)
    cep = models.CharField(max_length=10, blank=True, null=True)
    endereco = models.TextField(blank=True, null=True)
    ativo = models.BooleanField(default=True)
    faltas_consecutivas = models.SmallIntegerField(default=0)
    bloqueado_online = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'cliente'
        indexes = [
            models.Index(fields=['telefone'], name='idx_cliente_telefone'),
        ]

    def __str__(self):
        return self.nome_completo

    def registrar_falta(self):
        self.faltas_consecutivas += 1
        if self.faltas_consecutivas >= 3:
            self.bloqueado_online = True
        self.save()

    def resetar_faltas(self):
        self.faltas_consecutivas = 0
        self.bloqueado_online = False
        self.save()
