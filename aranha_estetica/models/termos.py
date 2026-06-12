# aranha_estetica/models/termos.py — Termos de consentimento
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
            ),
            # so 1 versao ativa por escopo (tipo+procedimento; LGPD = procedimento NULL)
            models.UniqueConstraint(
                fields=['tipo', 'procedimento'],
                condition=models.Q(ativa=True),
                name='uniq_termo_ativo_por_escopo',
            ),
            models.UniqueConstraint(
                fields=['tipo'],
                condition=models.Q(ativa=True) & models.Q(procedimento__isnull=True),
                name='uniq_termo_ativo_global',
            ),
        ]


class AceiteTermo(models.Model):
    """Aceite/assinatura de termo pelo cliente — LGPD ou procedimento.

    Unifica AceitePrivacidade + AssinaturaTermoProcedimento (remodelagem
    v2.1 fase 6): a distincao vive em versao_termo.tipo. atendimento so
    e preenchido em termos de procedimento. on_delete=PROTECT no cliente:
    aceite e evidencia legal (LGPD art. 8) — anonimizar, nunca cascatear.
    """
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='aceites')
    versao_termo = models.ForeignKey(VersaoTermo, on_delete=models.RESTRICT)
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.SET_NULL, blank=True, null=True,
    )
    ip = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=500, blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'aceite_termo'
        constraints = [
            models.UniqueConstraint(
                fields=['cliente', 'versao_termo'],
                name='uniq_aceite_cliente_versao',
            ),
        ]
        indexes = [
            models.Index(fields=['cliente', '-criado_em'], name='idx_aceite_cli_data'),
        ]

    def __str__(self):
        return f'Aceite {self.versao_termo} por cliente {self.cliente_id}'
