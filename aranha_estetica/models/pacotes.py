# aranha_estetica/models/pacotes.py — Pacotes de servicos
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Count

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


class CompraPacote(models.Model):
    STATUS_CHOICES = [
        ('ATIVO', 'Ativo'),
        ('FINALIZADO', 'Finalizado'),
        ('CANCELADO', 'Cancelado'),
        ('EXPIRADO', 'Expirado'),
    ]

    # PROTECT: compra e registro fiscal — nao cascateia com o cliente
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='pacotes_comprados')
    pacote = models.ForeignKey(Pacote, on_delete=models.RESTRICT)
    criado_em = models.DateTimeField(auto_now_add=True)
    valor_pago = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)],
    )
    status = models.CharField(max_length=20, default='ATIVO', choices=STATUS_CHOICES)
    data_expiracao = models.DateField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'compra_pacote'
        indexes = [
            models.Index(fields=['cliente', 'status'], name='idx_pacote_cli_status'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(status__in=['ATIVO', 'FINALIZADO', 'CANCELADO', 'EXPIRADO']),
                name='chk_pacote_cliente_status'
            ),
            models.CheckConstraint(
                check=models.Q(valor_pago__gte=0),
                name='chk_compra_pacote_valor_pago',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.data_expiracao and self.pacote and self.pacote.validade_meses:
            from django.utils import timezone
            from dateutil.relativedelta import relativedelta
            # localdate() como base evita off-by-one de fuso: timezone.now() e UTC,
            # e somar meses + .date() perto da meia-noite local deslocaria 1 dia.
            self.data_expiracao = (
                timezone.localdate() + relativedelta(months=self.pacote.validade_meses)
            )
        super().save(*args, **kwargs)

    def saldo_por_procedimento(self):
        """Sessoes por item do pacote: [{procedimento, total, usadas, restantes}].

        Fonte unica do saldo (ficha do cliente, e-mail de expiracao). Conta
        os ConsumoSessao da compra por procedimento numa unica query agregada
        — mesma regra de verificar_finalizacao (so ConsumoSessao conta; o
        retorno gratuito nao gera consumo). Em listas, prefetch
        'pacote__itens__procedimento' evita N+1.
        """
        usadas_por_proc = dict(
            self.sessoes_realizadas.values('atendimento__procedimento')
            .annotate(c=Count('id'))
            .values_list('atendimento__procedimento', 'c')
        )
        saldo = []
        for item in self.pacote.itens.all():
            usadas = usadas_por_proc.get(item.procedimento_id, 0)
            saldo.append({
                'procedimento': item.procedimento,
                'total': item.quantidade_sessoes,
                'usadas': usadas,
                'restantes': max(0, item.quantidade_sessoes - usadas),
            })
        return saldo

    def sessoes_restantes(self):
        """Total de sessoes ainda nao usadas (soma de saldo_por_procedimento)."""
        return sum(linha['restantes'] for linha in self.saldo_por_procedimento())

    def verificar_finalizacao(self):
        # Contagem agregada numa unica query (evita N+1 por item do pacote);
        # leitura + finalizacao sob lock para serializar consumos concorrentes.
        with transaction.atomic():
            travada = CompraPacote.objects.select_for_update().get(pk=self.pk)
            contagens = dict(
                travada.sessoes_realizadas.values('atendimento__procedimento')
                .annotate(c=Count('id'))
                .values_list('atendimento__procedimento', 'c')
            )
            for item in travada.pacote.itens.all():
                if contagens.get(item.procedimento_id, 0) < item.quantidade_sessoes:
                    return
            travada.status = 'FINALIZADO'
            travada.save()
        self.status = travada.status

    def __str__(self):
        cliente_nome = self.cliente.nome if self.cliente_id else 's/ cliente'
        pacote_nome = self.pacote.nome if self.pacote_id else 's/ pacote'
        return f'{cliente_nome} — {pacote_nome} ({self.get_status_display()})'


class ConsumoSessao(models.Model):
    compra_pacote = models.ForeignKey(
        CompraPacote, on_delete=models.CASCADE, related_name='sessoes_realizadas'
    )
    # OneToOne (NOT NULL) = UNIQUE: 1 atendimento consome no maximo 1 sessao
    atendimento = models.OneToOneField(
        'Atendimento', on_delete=models.RESTRICT, related_name='sessao_pacote_vinculada'
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'consumo_sessao'
        indexes = [
            models.Index(fields=['compra_pacote'], name='idx_sessao_pct_cli'),
        ]
