# aranha_estetica/models/agendamentos.py — Atendimentos e notificacoes
import secrets
from django.core.validators import MinValueValidator
from django.db import DatabaseError, IntegrityError, models
from django.utils import timezone

from .clientes import Cliente
from .procedimentos import Procedimento, Promocao
from .profissionais import Profissional


class AtendimentoQuerySet(models.QuerySet):
    """QuerySet com queries reutilizaveis — encapsula conhecimento de dominio."""

    def ativos(self):
        """Atendimentos nao cancelados/reagendados/faltados."""
        return self.exclude(status__in=['CANCELADO', 'REAGENDADO', 'FALTOU'])

    def futuros(self):
        return self.filter(data_hora_inicio__gte=timezone.now())

    def passados(self):
        return self.filter(data_hora_fim__lt=timezone.now())

    def hoje(self):
        hoje = timezone.localdate()
        return self.filter(data_hora_inicio__date=hoje)

    def pendentes_aprovacao(self):
        return self.filter(status='PENDENTE')

    def realizados(self):
        return self.filter(status='REALIZADO')

    def do_profissional(self, profissional):
        return self.filter(profissional=profissional)

    def conflito_com(self, profissional, data_inicio, data_fim):
        """Atendimentos que conflitam com janela [data_inicio, data_fim)."""
        return self.filter(
            profissional=profissional,
            data_hora_inicio__lt=data_fim,
            data_hora_fim__gt=data_inicio,
            status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO'],
        )


class AtendimentoManager(models.Manager):
    def get_queryset(self):
        return AtendimentoQuerySet(self.model, using=self._db)

    # Proxy methods do queryset p/ chamadas curtas
    def ativos(self): return self.get_queryset().ativos()
    def futuros(self): return self.get_queryset().futuros()
    def hoje(self): return self.get_queryset().hoje()
    def pendentes_aprovacao(self): return self.get_queryset().pendentes_aprovacao()
    def realizados(self): return self.get_queryset().realizados()
    def do_profissional(self, prof): return self.get_queryset().do_profissional(prof)
    def conflito_com(self, prof, data_inicio, data_fim):
        return self.get_queryset().conflito_com(prof, data_inicio, data_fim)


class Atendimento(models.Model):
    # Constantes publicas — referencia unica p/ status (substitui magic strings)
    STATUS_PENDENTE = 'PENDENTE'
    STATUS_AGENDADO = 'AGENDADO'
    STATUS_CONFIRMADO = 'CONFIRMADO'
    STATUS_REALIZADO = 'REALIZADO'
    STATUS_CANCELADO = 'CANCELADO'
    STATUS_FALTOU = 'FALTOU'
    STATUS_REAGENDADO = 'REAGENDADO'

    # Grupos para queries semanticas
    STATUS_FINALIZADOS = (STATUS_REALIZADO, STATUS_CANCELADO, STATUS_FALTOU, STATUS_REAGENDADO)
    STATUS_ATIVOS = (STATUS_PENDENTE, STATUS_AGENDADO, STATUS_CONFIRMADO)

    STATUS_CHOICES = [
        (STATUS_PENDENTE, 'Pendente de Confirmação'),
        (STATUS_AGENDADO, 'Agendado'),
        (STATUS_CONFIRMADO, 'Confirmado'),
        (STATUS_REALIZADO, 'Realizado'),
        (STATUS_CANCELADO, 'Cancelado'),
        (STATUS_FALTOU, 'Faltou'),
        (STATUS_REAGENDADO, 'Reagendado'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.RESTRICT)
    profissional = models.ForeignKey(Profissional, on_delete=models.RESTRICT)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.RESTRICT)
    promocao = models.ForeignKey(
        Promocao, on_delete=models.SET_NULL, blank=True, null=True
    )
    reagendado_de = models.ForeignKey(
        'self', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='reagendamentos'
    )
    # F-RET — vinculo entre sessao principal e retorno gratuito
    atendimento_origem = models.ForeignKey(
        'self', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='retornos',
    )
    eh_retorno = models.BooleanField(default=False, db_index=True)
    data_hora_inicio = models.DateTimeField()
    data_hora_fim = models.DateTimeField()
    valor_cobrado = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        validators=[MinValueValidator(0)],
    )
    valor_original = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        validators=[MinValueValidator(0)],
    )
    descricao_preco = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, default='PENDENTE', choices=STATUS_CHOICES)
    token_cancelamento = models.CharField(
        max_length=64, unique=True, blank=True, null=True, db_index=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True, null=True)

    def save(self, *args, **kwargs):
        if not self.token_cancelamento:
            self.token_cancelamento = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    # ───────── FSM transitions (hybrid: campo continua CharField) ─────────
    # Garante transicoes validas. Codigo legado que faz `status = X; save()`
    # continua funcionando — uso desses metodos eh recomendado em codigo novo.

    TRANSICOES = {
        'PENDENTE': {'AGENDADO', 'CONFIRMADO', 'CANCELADO', 'REAGENDADO'},
        'AGENDADO': {'CONFIRMADO', 'CANCELADO', 'FALTOU', 'REAGENDADO', 'REALIZADO'},
        'CONFIRMADO': {'REALIZADO', 'CANCELADO', 'FALTOU', 'REAGENDADO'},
        'REALIZADO': set(),
        'CANCELADO': set(),
        'FALTOU': set(),
        'REAGENDADO': set(),
    }

    class TransicaoInvalida(Exception):
        pass

    def _transicionar(self, novo_status, motivo=None, by_user=None):
        permitido = self.TRANSICOES.get(self.status, set())
        if novo_status not in permitido:
            raise self.TransicaoInvalida(
                f'Transicao {self.status} -> {novo_status} nao permitida'
            )
        anterior = self.status
        self.status = novo_status
        self.save(update_fields=['status', 'atualizado_em'])
        try:
            from .sistema import LogAuditoria
            LogAuditoria.objects.create(
                usuario=by_user,
                acao=f'Atendimento {self.pk}: {anterior} -> {novo_status}'
                     + (f' ({motivo})' if motivo else ''),
                tabela='atendimento',
                registro_id=self.pk,
            )
        except (DatabaseError, IntegrityError) as exc:
            # Auditoria best-effort — falha de DB nao bloqueia transicao de status
            import logging
            logging.getLogger(__name__).warning(
                'log_auditoria_falhou',
                extra={'atendimento_id': self.pk, 'erro': str(exc)},
            )

    def confirmar(self, by_user=None):
        self._transicionar('CONFIRMADO', by_user=by_user)
        self._publish_event('AtendimentoConfirmado', confirmado_por_id=getattr(by_user, 'pk', None))

    def cancelar(self, motivo='', by_user=None):
        self._transicionar('CANCELADO', motivo=motivo, by_user=by_user)
        self._publish_event(
            'AtendimentoCancelado',
            motivo=motivo or '',
            cancelado_por_cliente=(by_user is None),
        )

    def marcar_realizado(self, by_user=None):
        self._transicionar('REALIZADO', by_user=by_user)
        self._publish_event(
            'AtendimentoRealizado',
            cliente_id=self.cliente_id,
            profissional_id=self.profissional_id,
        )

    def marcar_falta(self, by_user=None):
        self._transicionar('FALTOU', by_user=by_user)
        self._publish_event('AtendimentoFaltou', cliente_id=self.cliente_id)

    def marcar_reagendado(self, by_user=None):
        self._transicionar('REAGENDADO', by_user=by_user)

    def aprovar(self, by_user=None):
        self._transicionar('AGENDADO', by_user=by_user)

    def _publish_event(self, event_name: str, **fields) -> None:
        """Publica DomainEvent via bus. Best-effort — falha nao quebra transicao."""
        try:
            from django.utils import timezone
            from ..domain import event_bus, events as domain_events
            event_cls = getattr(domain_events, event_name, None)
            if not event_cls:
                return
            event_bus.EventBus.publish(event_cls(
                occurred_at=timezone.now(),
                atendimento_id=self.pk,
                **fields,
            ))
        except Exception:  # pylint: disable=broad-except
            pass  # bus best-effort

    objects = AtendimentoManager()

    class Meta:
        managed = True
        db_table = 'atendimento'
        indexes = [
            models.Index(fields=['status'], name='idx_atendimento_status'),
            models.Index(fields=['data_hora_inicio'], name='idx_atendimento_data'),
            models.Index(fields=['cliente', 'status'], name='idx_atendimento_cli_status'),
            # idx_atendimento_cliente removido — prefixo de idx_atendimento_cli_status cobre
            models.Index(fields=['profissional', 'data_hora_inicio'], name='idx_atend_prof_data'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(status__in=[
                    'PENDENTE', 'AGENDADO', 'CONFIRMADO', 'REALIZADO',
                    'CANCELADO', 'FALTOU', 'REAGENDADO',
                ]),
                name='chk_atendimento_status_v2'
            ),
            models.CheckConstraint(
                check=models.Q(data_hora_fim__gt=models.F('data_hora_inicio')),
                name='chk_atendimento_fim_apos_inicio',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(eh_retorno=False) | models.Q(atendimento_origem__isnull=False)
                ),
                name='chk_retorno_tem_origem',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(eh_retorno=False) | models.Q(valor_cobrado=0) |
                    models.Q(valor_cobrado__isnull=True)
                ),
                name='chk_retorno_valor_zero',
            ),
        ]

    def __str__(self):
        data_fmt = self.data_hora_inicio.strftime('%d/%m/%Y %H:%M') if self.data_hora_inicio else 's/ data'
        cliente_nome = self.cliente.nome if self.cliente_id else 's/ cliente'
        proc_nome = self.procedimento.nome if self.procedimento_id else 's/ procedimento'
        return f'{data_fmt} — {cliente_nome} ({proc_nome})'


class Notificacao(models.Model):
    TIPO_CHOICES = [
        ('LEMBRETE', 'Lembrete D-1'),
        ('LEMBRETE_2H', 'Lembrete T-2h'),
        ('CONFIRMACAO', 'Confirmação'),
        ('CANCELAMENTO', 'Cancelamento'),
        ('NPS', 'Pesquisa NPS'),
        ('PESQUISA', 'Pesquisa de satisfação detalhada'),
        ('APROVACAO', 'Aprovação Profissional'),
    ]
    CANAL_CHOICES = [
        ('WHATSAPP', 'WhatsApp'),
        ('SMS', 'SMS'),
        ('EMAIL', 'E-mail'),
    ]
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('ENVIADO', 'Enviado'),
        ('FALHOU', 'Falhou'),
    ]
    RESPOSTA_CHOICES = [
        ('CONFIRMOU', 'Confirmou'),
        ('CANCELOU', 'Cancelou'),
    ]

    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=30, default='LEMBRETE', choices=TIPO_CHOICES)
    canal = models.CharField(max_length=20, default='WHATSAPP', choices=CANAL_CHOICES)
    status = models.CharField(max_length=20, default='PENDENTE', choices=STATUS_CHOICES)
    resposta = models.CharField(
        max_length=20, blank=True, null=True, choices=RESPOSTA_CHOICES
    )
    token = models.CharField(max_length=64, unique=True, blank=True, null=True)
    mensagem = models.TextField(blank=True, null=True)
    enviado_em = models.DateTimeField(blank=True, null=True)
    respondido_em = models.DateTimeField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'notificacao'
        indexes = [
            models.Index(fields=['tipo', 'status'], name='idx_notificacao_tipo_status'),
            models.Index(fields=['-criado_em'], name='idx_notificacao_criado'),
            models.Index(fields=['tipo', 'canal', 'status', '-criado_em'], name='idx_notif_nps_lookup'),
            models.Index(fields=['atendimento', 'tipo'], name='idx_notif_atend_tipo'),
        ]

    def __str__(self):
        data_fmt = self.criado_em.strftime('%d/%m/%Y %H:%M') if self.criado_em else 's/ data'
        cliente_nome = (
            self.atendimento.cliente.nome
            if self.atendimento_id and self.atendimento.cliente_id
            else 's/ cliente'
        )
        return f'{self.get_tipo_display()} — {cliente_nome} ({data_fmt})'
