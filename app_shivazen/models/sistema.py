# app_shivazen/models/sistema.py — Auditoria, configuracao, lista de espera, verificacao
from django.db import models

from .clientes import Cliente
from .procedimentos import Procedimento
from .profissionais import Profissional


class ListaEspera(models.Model):
    TURNO_CHOICES = [
        ('MANHA', 'Manhã'),
        ('TARDE', 'Tarde'),
        ('NOITE', 'Noite'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE)
    profissional_desejado = models.ForeignKey(
        Profissional, on_delete=models.SET_NULL, blank=True, null=True
    )
    data_desejada = models.DateField()
    turno_desejado = models.CharField(
        max_length=20, blank=True, null=True, choices=TURNO_CHOICES
    )
    notificado = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    token_reserva = models.CharField(max_length=64, blank=True, null=True)
    expira_em = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'lista_espera'
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(turno_desejado__isnull=True) |
                    models.Q(turno_desejado__in=['MANHA', 'TARDE', 'NOITE'])
                ),
                name='chk_lista_espera_turno'
            )
        ]

    def __str__(self):
        cliente_nome = self.cliente.nome_completo if self.cliente_id else 's/ cliente'
        proc_nome = self.procedimento.nome if self.procedimento_id else 's/ procedimento'
        data = self.data_desejada.strftime('%d/%m/%Y') if self.data_desejada else 's/ data'
        return f'{cliente_nome} — {proc_nome} ({data})'


class LogAuditoria(models.Model):
    usuario = models.ForeignKey('Usuario', on_delete=models.SET_NULL, blank=True, null=True)
    acao = models.TextField()
    tabela_afetada = models.CharField(max_length=100, blank=True, null=True)
    id_registro_afetado = models.IntegerField(blank=True, null=True)
    detalhes = models.JSONField(blank=True, null=True)
    ip_origem = models.GenericIPAddressField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'log_auditoria'
        indexes = [
            models.Index(fields=['tabela_afetada'], name='idx_auditoria_tabela'),
            models.Index(fields=['-criado_em'], name='idx_auditoria_criado'),
            models.Index(fields=['usuario', '-criado_em'], name='idx_auditoria_user_data'),
        ]


class ConfiguracaoSistema(models.Model):
    chave = models.CharField(max_length=100, unique=True)
    valor = models.TextField(blank=True, null=True)
    descricao = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'configuracao_sistema'

    def __str__(self):
        return f'{self.chave}: {self.valor}'


class CodigoVerificacao(models.Model):
    TTL_SEGUNDOS = 600  # 10 minutos

    telefone = models.CharField(max_length=20)
    codigo = models.CharField(max_length=6)
    criado_em = models.DateTimeField(auto_now_add=True)
    usado = models.BooleanField(default=False)

    class Meta:
        managed = True
        db_table = 'codigo_verificacao'

    @property
    def esta_valido(self):
        from django.utils import timezone
        return (
            not self.usado
            and (timezone.now() - self.criado_em).total_seconds() < self.TTL_SEGUNDOS
        )

    @classmethod
    def consumir(cls, telefone, codigo):
        """Valida e marca usado atomicamente."""
        from datetime import timedelta
        from django.db import connection, transaction
        from django.utils import timezone

        limite = timezone.now() - timedelta(seconds=cls.TTL_SEGUNDOS)

        with transaction.atomic():
            qs = cls.objects.filter(
                telefone=telefone,
                codigo=codigo,
                usado=False,
                criado_em__gte=limite,
            ).order_by('-criado_em')

            if connection.vendor != 'sqlite':
                qs = qs.select_for_update(skip_locked=True)

            row = qs.first()
            if not row:
                return False
            row.usado = True
            row.save(update_fields=['usado'])
        return True
