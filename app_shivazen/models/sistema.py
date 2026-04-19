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
        indexes = [
            models.Index(fields=['cliente'], name='idx_espera_cliente'),
            models.Index(fields=['procedimento', 'data_desejada', 'notificado'], name='idx_espera_proc_data_notif'),
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


class Feriado(models.Model):
    """Datas bloqueadas para agendamento — feriados nacionais/locais e recessos da clinica."""

    ESCOPO_NACIONAL = 'NACIONAL'
    ESCOPO_LOCAL = 'LOCAL'
    ESCOPO_CLINICA = 'CLINICA'
    ESCOPO_CHOICES = [
        (ESCOPO_NACIONAL, 'Nacional'),
        (ESCOPO_LOCAL, 'Local (estadual/municipal)'),
        (ESCOPO_CLINICA, 'Recesso da clinica'),
    ]

    data = models.DateField()
    nome = models.CharField(max_length=120)
    escopo = models.CharField(max_length=20, choices=ESCOPO_CHOICES, default=ESCOPO_NACIONAL)
    bloqueia_agendamento = models.BooleanField(
        default=True,
        help_text='Se True, impede geracao de horarios livres nesta data.',
    )
    observacao = models.CharField(max_length=255, blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'feriado'
        constraints = [
            models.UniqueConstraint(fields=['data', 'escopo'], name='uq_feriado_data_escopo'),
        ]
        indexes = [
            models.Index(fields=['data'], name='idx_feriado_data'),
        ]
        ordering = ['data']

    def __str__(self):
        return f'{self.data.strftime("%d/%m/%Y")} — {self.nome}'


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


class OtpCode(models.Model):
    """OTP por email ou SMS: codigo hashed, TTL, rate limit por tentativas, IP."""

    PROPOSITO_AGENDAMENTO = 'AGENDAMENTO'
    PROPOSITO_LOGIN = 'LOGIN_CLIENTE'
    PROPOSITO_CHOICES = [
        (PROPOSITO_AGENDAMENTO, 'Agendamento'),
        (PROPOSITO_LOGIN, 'Login cliente'),
    ]

    CANAL_EMAIL = 'EMAIL'
    CANAL_SMS = 'SMS'
    CANAL_CHOICES = [(CANAL_EMAIL, 'Email'), (CANAL_SMS, 'SMS')]

    import os as _os
    TTL_SEGUNDOS = int(_os.environ.get('OTP_TTL_SEGUNDOS', '600'))  # 10 min default
    MAX_TENTATIVAS = int(_os.environ.get('OTP_MAX_TENTATIVAS', '5'))
    REENVIO_MINIMO_SEG = int(_os.environ.get('OTP_REENVIO_MINIMO_SEG', '60'))

    email = models.EmailField()
    telefone = models.CharField(max_length=20, blank=True, null=True)
    canal = models.CharField(max_length=10, choices=CANAL_CHOICES, default=CANAL_EMAIL)
    codigo_hash = models.CharField(max_length=64)  # sha256 hex
    proposito = models.CharField(max_length=20, choices=PROPOSITO_CHOICES, default=PROPOSITO_AGENDAMENTO)
    criado_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField()
    usado_em = models.DateTimeField(blank=True, null=True)
    tentativas = models.PositiveSmallIntegerField(default=0)
    ip_origem = models.GenericIPAddressField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'otp_code'
        indexes = [
            models.Index(fields=['email', '-criado_em'], name='idx_otp_email_data'),
            models.Index(fields=['proposito', '-criado_em'], name='idx_otp_prop_data'),
        ]

    def __str__(self):
        return f'OTP {self.email} ({self.proposito})'

    @property
    def esta_valido(self):
        from django.utils import timezone
        return (
            self.usado_em is None
            and self.expira_em > timezone.now()
            and self.tentativas < self.MAX_TENTATIVAS
        )

    @classmethod
    def pode_reenviar(cls, email, proposito=PROPOSITO_AGENDAMENTO):
        """True se passou REENVIO_MINIMO_SEG desde ultimo codigo."""
        from datetime import timedelta
        from django.utils import timezone
        limite = timezone.now() - timedelta(seconds=cls.REENVIO_MINIMO_SEG)
        return not cls.objects.filter(
            email=email, proposito=proposito, criado_em__gt=limite
        ).exists()

    @classmethod
    def gerar(cls, email, ip=None, proposito=PROPOSITO_AGENDAMENTO, canal=CANAL_EMAIL, telefone=None):
        """Invalida anteriores, cria novo. Retorna (codigo_plano, obj)."""
        import hashlib
        import secrets
        from datetime import timedelta
        from django.utils import timezone

        codigo = f'{secrets.randbelow(1_000_000):06d}'
        codigo_hash = hashlib.sha256(codigo.encode()).hexdigest()
        agora = timezone.now()

        cls.objects.filter(
            email=email, proposito=proposito, usado_em__isnull=True
        ).update(usado_em=agora)

        obj = cls.objects.create(
            email=email,
            telefone=telefone,
            canal=canal,
            codigo_hash=codigo_hash,
            proposito=proposito,
            expira_em=agora + timedelta(seconds=cls.TTL_SEGUNDOS),
            ip_origem=ip,
        )
        return codigo, obj

    @classmethod
    def verificar(cls, email, codigo, proposito=PROPOSITO_AGENDAMENTO):
        """Consome atomicamente. Retorna (ok, motivo)."""
        import hashlib
        from django.db import connection, transaction
        from django.utils import timezone

        codigo_hash = hashlib.sha256((codigo or '').encode()).hexdigest()

        with transaction.atomic():
            qs = cls.objects.filter(
                email=email,
                proposito=proposito,
                usado_em__isnull=True,
                expira_em__gt=timezone.now(),
            ).order_by('-criado_em')
            if connection.vendor != 'sqlite':
                qs = qs.select_for_update(skip_locked=True)

            obj = qs.first()
            if not obj:
                return False, 'expirado'
            if obj.tentativas >= cls.MAX_TENTATIVAS:
                obj.usado_em = timezone.now()
                obj.save(update_fields=['usado_em'])
                return False, 'bloqueado'
            if obj.codigo_hash != codigo_hash:
                obj.tentativas += 1
                obj.save(update_fields=['tentativas'])
                restante = cls.MAX_TENTATIVAS - obj.tentativas
                return False, f'incorreto:{restante}'
            obj.usado_em = timezone.now()
            obj.save(update_fields=['usado_em'])
            return True, 'ok'
