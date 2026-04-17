# app_shivazen/models/termos.py — Termos de consentimento
from django.db import models

from .clientes import Cliente
from .procedimentos import Procedimento


class VersaoTermo(models.Model):
    """Template versionado de um termo (LGPD ou por tipo de procedimento)."""
    TIPO_CHOICES = [
        ('LGPD', 'LGPD / Privacidade'),
        ('PROCEDIMENTO', 'Termo de Procedimento'),
    ]

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.CASCADE, blank=True, null=True
    )
    titulo = models.TextField()
    conteudo = models.TextField()
    versao = models.CharField(max_length=20)
    vigente_desde = models.DateField()
    ativa = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'versao_termo'
        constraints = [
            models.CheckConstraint(
                check=models.Q(tipo__in=['LGPD', 'PROCEDIMENTO']),
                name='chk_versao_termo_tipo'
            )
        ]


class AceitePrivacidade(models.Model):
    """LGPD: cliente assina uma vez por versao de termo de privacidade."""
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    versao_termo = models.ForeignKey(VersaoTermo, on_delete=models.RESTRICT)
    ip = models.CharField(max_length=45, blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'aceite_privacidade'
        unique_together = (('cliente', 'versao_termo'),)


class AssinaturaTermoProcedimento(models.Model):
    """Termo de procedimento: assinado uma vez por cliente por versao."""
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    versao_termo = models.ForeignKey(VersaoTermo, on_delete=models.RESTRICT)
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.SET_NULL, blank=True, null=True
    )
    ip = models.CharField(max_length=45, blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'assinatura_termo_procedimento'
        unique_together = (('cliente', 'versao_termo'),)
