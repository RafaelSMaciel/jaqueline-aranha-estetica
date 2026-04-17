# app_shivazen/models/pacotes.py — Pacotes de servicos
from django.db import models

from .clientes import Cliente
from .procedimentos import Procedimento


class Pacote(models.Model):
    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True, null=True)
    preco_total = models.DecimalField(max_digits=10, decimal_places=2)
    ativo = models.BooleanField(default=True)
    validade_meses = models.SmallIntegerField(default=12)

    class Meta:
        managed = True
        db_table = 'pacote'
        constraints = [
            models.CheckConstraint(
                check=models.Q(preco_total__gte=0),
                name='chk_pacote_preco_positivo'
            ),
            models.CheckConstraint(
                check=models.Q(validade_meses__gt=0),
                name='chk_pacote_validade_positiva'
            ),
        ]

    def __str__(self):
        return self.nome


class ItemPacote(models.Model):
    pacote = models.ForeignKey(Pacote, on_delete=models.CASCADE, related_name='itens')
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE)
    quantidade_sessoes = models.SmallIntegerField(default=1)

    class Meta:
        managed = True
        db_table = 'item_pacote'
        unique_together = (('pacote', 'procedimento'),)
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantidade_sessoes__gt=0),
                name='chk_item_pacote_qtd_positiva'
            ),
        ]


class PacoteCliente(models.Model):
    STATUS_CHOICES = [
        ('ATIVO', 'Ativo'),
        ('FINALIZADO', 'Finalizado'),
        ('CANCELADO', 'Cancelado'),
        ('EXPIRADO', 'Expirado'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='pacotes_comprados')
    pacote = models.ForeignKey(Pacote, on_delete=models.RESTRICT)
    criado_em = models.DateTimeField(auto_now_add=True)
    valor_pago = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, default='ATIVO', choices=STATUS_CHOICES)
    data_expiracao = models.DateField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'pacote_cliente'
        indexes = [
            models.Index(fields=['cliente', 'status'], name='idx_pacote_cli_status'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(status__in=['ATIVO', 'FINALIZADO', 'CANCELADO', 'EXPIRADO']),
                name='chk_pacote_cliente_status'
            )
        ]

    def save(self, *args, **kwargs):
        if not self.data_expiracao and self.pacote and self.pacote.validade_meses:
            from django.utils import timezone
            from dateutil.relativedelta import relativedelta
            self.data_expiracao = (
                timezone.now() + relativedelta(months=self.pacote.validade_meses)
            ).date()
        super().save(*args, **kwargs)

    def verificar_finalizacao(self):
        for item in self.pacote.itens.all():
            sessoes_feitas = self.sessoes_realizadas.filter(
                atendimento__procedimento=item.procedimento
            ).count()
            if sessoes_feitas < item.quantidade_sessoes:
                return
        self.status = 'FINALIZADO'
        self.save()

    def __str__(self):
        cliente_nome = self.cliente.nome_completo if self.cliente_id else 's/ cliente'
        pacote_nome = self.pacote.nome if self.pacote_id else 's/ pacote'
        return f'{cliente_nome} — {pacote_nome} ({self.get_status_display()})'


class SessaoPacote(models.Model):
    pacote_cliente = models.ForeignKey(
        PacoteCliente, on_delete=models.CASCADE, related_name='sessoes_realizadas'
    )
    atendimento = models.OneToOneField(
        'Atendimento', on_delete=models.RESTRICT, related_name='sessao_pacote_vinculada'
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'sessao_pacote'
