# aranha_estetica/models/sistema.py — Auditoria, configuracao, lista de espera, verificacao
import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import connection, models, transaction
from django.db.models.functions import Now
from django.utils import timezone

from ..db.campos import HashSha256Field
from ..db.indices import Indice

from .clientes import Cliente
from .mixins import atualizado_em_legado, criado_em_legado
from .procedimentos import Procedimento
from .profissionais import Profissional


class ListaEspera(models.Model):
    TURNO_CHOICES = [
        ('MANHA', 'Manhã'),
        ('TARDE', 'Tarde'),
        ('NOITE', 'Noite'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, db_index=False)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.CASCADE, db_index=False)
    profissional_desejado = models.ForeignKey(
        Profissional, on_delete=models.SET_NULL, blank=True, null=True, db_index=False,
    )
    data_desejada = models.DateField()
    turno_desejado = models.CharField(
        max_length=20, blank=True, null=True, choices=TURNO_CHOICES, db_comment='NULL = qualquer turno.',
    )
    notificado = models.BooleanField(default=False, db_default=False, db_column='eh_notificado')
    # E-mail informado no formulario publico quando o cliente ja existe: avisar
    # por aqui sem reescrever o cadastro (uso: email_contato or cliente.email).
    # Ausente = NULL (nunca ''; ck_lista_espera_email_contato_nao_vazio).
    email_contato = models.EmailField(
        blank=True, null=True,
        db_comment='E-mail digitado no formulario publico quando o cliente ja existe (nao reescreve o cadastro).',
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'lista_espera'
        db_table_comment = 'Pedido de aviso de vaga. Apagado quando data_desejada passa (sp_aplicar_retencao_lgpd).'
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(turno_desejado__isnull=True) |
                    models.Q(turno_desejado__in=['MANHA', 'TARDE', 'NOITE'])
                ),
                name='ck_lista_espera_turno'
            ),
            models.CheckConstraint(
                condition=models.Q(email_contato__isnull=True) | ~models.Q(email_contato=''),
                name='ck_lista_espera_email_contato_nao_vazio',
            ),
            # cliente nao entra 2x na mesma fila enquanto nao notificado
            models.UniqueConstraint(
                fields=['cliente', 'procedimento', 'data_desejada'],
                condition=models.Q(notificado=False),
                name='uq_lista_espera_pendente',
            ),
        ]
        indexes = [
            Indice(fields=['cliente'], name='ix_lista_espera_cliente'),
            # candidatos FIFO (procedimento + data, nao notificado): o UNIQUE parcial tambem cobre
            Indice(fields=['procedimento', 'data_desejada'], name='ix_lista_espera_procedimento_data_desejada'),
            Indice(fields=['data_desejada'], name='ix_lista_espera_data_desejada'),
            Indice(
                fields=['profissional_desejado'], condition=models.Q(profissional_desejado__isnull=False),
                name='ix_lista_espera_profissional_desejado_parcial',
            ),
        ]

    def save(self, *args, **kwargs):
        self.email_contato = self.email_contato or None  # '' -> NULL (CHECK nao vazio)
        super().save(*args, **kwargs)

    def __str__(self):
        cliente_nome = self.cliente.nome if self.cliente_id else 's/ cliente'
        proc_nome = self.procedimento.nome if self.procedimento_id else 's/ procedimento'
        data = self.data_desejada.strftime('%d/%m/%Y') if self.data_desejada else 's/ data'
        return f'{cliente_nome} — {proc_nome} ({data})'


def rotulo_usuario(usuario) -> str:
    """'Nome <email>' do autor — snapshot gravado em LogAuditoria.usuario_nome."""
    nome = (getattr(usuario, 'nome', '') or '').strip()
    email = (getattr(usuario, 'email', '') or '').strip()
    if nome and email:
        return f'{nome} <{email}>'[:255]
    return (nome or email)[:255]


class LogAuditoria(models.Model):
    """Trilha de auditoria (LGPD art. 37).

    usuario SET_NULL + usuario_nome: o snapshot 'Nome <email>' do autor e
    gravado no save (registrar_log incluso) — excluir ou renomear o Usuario
    nao transforma a acao dele em "acao do sistema". O Django admin nao exclui
    Usuario (desativar = ativo=False).
    """
    # sem indice de FK: autoria (spec §2.11 — usuario nunca e excluido; nenhuma tela filtra por autor)
    usuario = models.ForeignKey(
        'Usuario', on_delete=models.SET_NULL, blank=True, null=True, db_column='usuario_autor_id',
        db_index=False,
    )
    usuario_nome = models.CharField(
        max_length=255, blank=True, default='', db_default='', db_column='usuario_autor_nome',
        db_comment='Snapshot "Nome <email>" do autor no momento da acao.',
    )
    acao = models.TextField()
    tabela = models.CharField(max_length=100, blank=True, default='', db_default='')
    # bigint: aponta p/ PKs bigint de qualquer tabela (era integer ate a 0054)
    registro_id = models.BigIntegerField(
        blank=True, null=True, db_column='registro_pk',
        db_comment='PK do registro afetado na tabela indicada em "tabela" (referencia polimorfica, sem FK).',
    )
    detalhes = models.JSONField(blank=True, null=True)
    ip_origem = models.GenericIPAddressField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())

    class Meta:
        managed = True
        db_table = 'log_auditoria'
        db_table_comment = (
            'Trilha de ACOES da aplicacao (LGPD art. 37): autor + snapshot "Nome <email>", acao, alvo, IP. '
            'Retencao 5 anos. A trilha de DADOS (antes/depois) fica em historico_alteracao.'
        )
        indexes = [
            Indice(fields=['-criado_em'], name='ix_log_auditoria_criado_em'),
            Indice(fields=['tabela', 'registro_id'], name='ix_log_auditoria_tabela_registro'),
        ]

    def save(self, *args, **kwargs):
        if self.usuario_id and not self.usuario_nome:
            self.usuario_nome = rotulo_usuario(self.usuario)
            update_fields = kwargs.get('update_fields')
            if update_fields is not None and 'usuario_nome' not in update_fields:
                kwargs['update_fields'] = [*update_fields, 'usuario_nome']
        super().save(*args, **kwargs)


# Feriados/recessos: AgendaExcecao FOLGA da clinica (profissional NULL, origem
# FERIADO_NACIONAL/FERIADO_LOCAL/RECESSO) — models/agenda.py, migration 0048.


class Configuracao(models.Model):
    chave = models.CharField(max_length=100)
    valor = models.TextField(blank=True, default='', db_default='')
    descricao = models.TextField(blank=True, default='', db_default='')
    criado_em = criado_em_legado()
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'configuracao'
        db_table_comment = (
            'Chave-valor editavel no painel: Branding, email_admin, MAX_FALTAS_BLOQUEIO. '
            '(prontuario_perguntas saiu p/ formulario tipo PRONTUARIO.)'
        )
        constraints = [
            models.UniqueConstraint(fields=['chave'], name='uq_configuracao_chave'),
        ]

    def __str__(self):
        return f'{self.chave}: {self.valor}'


# CodigoVerificacao removido na remodelagem v2.1 fase 1b — guardava OTP em
# TEXTO PLANO e sem contador de tentativas. Substituido por CodigoOtp
# (HMAC-SHA256 + lockout). Usado pelo booking (agendamento/login) e por
# lgpd.meus_dados (proposito DSAR).


class CodigoOtp(models.Model):
    """OTP por email ou SMS: codigo hashed, TTL, rate limit por tentativas, IP.

    Retencao: linhas guardam email/telefone/IP — purga apos
    settings.RETENCAO_OTP_HORAS (job de manutencao, spec remodelagem §7).
    """

    PROPOSITO_AGENDAMENTO = 'AGENDAMENTO'
    PROPOSITO_LOGIN = 'LOGIN_CLIENTE'
    PROPOSITO_DSAR = 'DSAR'
    PROPOSITO_CHOICES = [
        (PROPOSITO_AGENDAMENTO, 'Agendamento'),
        (PROPOSITO_LOGIN, 'Login cliente'),
        (PROPOSITO_DSAR, 'Export de dados LGPD'),
    ]

    CANAL_EMAIL = 'EMAIL'
    CANAL_SMS = 'SMS'
    CANAL_CHOICES = [(CANAL_EMAIL, 'Email'), (CANAL_SMS, 'SMS')]

    TTL_SEGUNDOS = int(os.environ.get('OTP_TTL_SEGUNDOS', '600'))  # 10 min default
    MAX_TENTATIVAS = int(os.environ.get('OTP_MAX_TENTATIVAS', '5'))
    REENVIO_MINIMO_SEG = int(os.environ.get('OTP_REENVIO_MINIMO_SEG', '60'))

    # chave do desafio: e-mail ou o pseudo-e-mail 'sms+<digitos>@shivazen.local' (coluna identificador)
    email = models.EmailField(
        db_column='identificador',
        db_comment='Chave do desafio: e-mail, ou "sms+<digitos>@shivazen.local" nos fluxos so-telefone.',
    )
    telefone = models.CharField(max_length=20, blank=True, null=True)
    canal = models.CharField(max_length=10, choices=CANAL_CHOICES, default=CANAL_SMS, db_default=CANAL_SMS)
    codigo_hash = HashSha256Field()  # HMAC-SHA256 hex (chave = SECRET_KEY), char(64)
    proposito = models.CharField(
        max_length=20, choices=PROPOSITO_CHOICES, default=PROPOSITO_AGENDAMENTO, db_default=PROPOSITO_AGENDAMENTO,
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    expira_em = models.DateTimeField()
    usado_em = models.DateTimeField(blank=True, null=True)
    tentativas = models.SmallIntegerField(
        default=0, db_default=0, validators=[MinValueValidator(0)], db_column='qtd_tentativas',
    )
    ip_origem = models.GenericIPAddressField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'codigo_otp'
        db_table_comment = (
            'Desafio OTP (HMAC-SHA256 com a SECRET_KEY), TTL e lockout. Efemero: apagado apos 24h '
            '(sp_purgar_dados_expirados).'
        )
        constraints = [
            models.CheckConstraint(condition=models.Q(canal__in=['EMAIL', 'SMS']), name='ck_codigo_otp_canal'),
            models.CheckConstraint(
                condition=models.Q(proposito__in=['AGENDAMENTO', 'LOGIN_CLIENTE', 'DSAR']),
                name='ck_codigo_otp_proposito',
            ),
            # Teto de banco (10) > MAX_TENTATIVAS logico (default 5) de proposito:
            # o lockout aplicativo bloqueia antes; o CHECK e apenas guarda-corpo
            # contra valores absurdos vindos de escrita direta.
            models.CheckConstraint(
                condition=models.Q(tentativas__gte=0) & models.Q(tentativas__lte=10),
                name='ck_codigo_otp_qtd_tentativas_0_10',
            ),
        ]
        indexes = [
            Indice(fields=['email', 'proposito', '-criado_em'], name='ix_codigo_otp_identificador_proposito_criado_em'),
        ]

    def __str__(self):
        return f'OTP {self.email} ({self.proposito})'

    @staticmethod
    def _hash_codigo(email: str, codigo: str) -> str:
        """HMAC-SHA256(SECRET_KEY, email:codigo). Sem a chave, um dump do
        banco nao permite testar os 10^6 codigos offline (sha256 puro permitia)."""
        msg = f'{email}:{codigo or ""}'.encode()
        return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()

    @staticmethod
    def email_para_telefone(telefone: str) -> str:
        """Pseudo-email canonico p/ fluxos telefone-only (chave do challenge).

        Mesmo padrao usado no booking publico: 'sms+<digitos>@shivazen.local'.
        Centralizado aqui p/ os fluxos DSAR / meus-agendamentos nao divergirem;
        normaliza como Cliente.telefone (+55/zero de tronco nao geram outra chave).
        """
        from ..validators import normalizar_telefone
        return f'sms+{normalizar_telefone(telefone)}@shivazen.local'

    @classmethod
    def gerar_sms(cls, telefone, ip=None, proposito=PROPOSITO_AGENDAMENTO):
        """Gera OTP p/ fluxo telefone-only. Retorna (codigo_plano, obj)."""
        return cls.gerar(
            cls.email_para_telefone(telefone),
            ip=ip, proposito=proposito, canal=cls.CANAL_SMS, telefone=telefone,
        )

    @classmethod
    def verificar_sms(cls, telefone, codigo, proposito=PROPOSITO_AGENDAMENTO):
        """Consome OTP de fluxo telefone-only. Retorna (ok, motivo)."""
        return cls.verificar(cls.email_para_telefone(telefone), codigo, proposito=proposito)

    @classmethod
    def pode_reenviar(cls, email, proposito=PROPOSITO_AGENDAMENTO):
        """True se passou REENVIO_MINIMO_SEG desde ultimo codigo."""
        limite = timezone.now() - timedelta(seconds=cls.REENVIO_MINIMO_SEG)
        return not cls.objects.filter(
            email=email, proposito=proposito, criado_em__gt=limite
        ).exists()

    @classmethod
    def gerar(cls, email, ip=None, proposito=PROPOSITO_AGENDAMENTO, canal=CANAL_SMS, telefone=None):
        """Invalida anteriores, cria novo. Retorna (codigo_plano, obj)."""
        codigo = f'{secrets.randbelow(1_000_000):06d}'
        codigo_hash = cls._hash_codigo(email, codigo)
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
        codigo_hash = cls._hash_codigo(email, (codigo or '').strip())

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
            if not hmac.compare_digest(obj.codigo_hash, codigo_hash):
                obj.tentativas += 1
                obj.save(update_fields=['tentativas'])
                restante = cls.MAX_TENTATIVAS - obj.tentativas
                return False, f'incorreto:{restante}'
            obj.usado_em = timezone.now()
            obj.save(update_fields=['usado_em'])
            return True, 'ok'
