# aranha_estetica/models/agendamentos.py — Atendimentos e notificacoes
import logging
import secrets

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import DatabaseError, IntegrityError, models, transaction
from django.utils import timezone

from ..utils.datas import fmt_local
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

    def conflito_com(self, profissional, data_inicio, data_fim):
        """Atendimentos que conflitam com janela [data_inicio, data_fim)."""
        return self.filter(
            profissional=profissional,
            data_hora_inicio__lt=data_fim,
            data_hora_fim__gt=data_inicio,
            status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO'],
        )


# from_queryset expoe automaticamente todos os metodos do QuerySet no manager
# (ativos/futuros/passados/hoje/conflito_com/...), eliminando os proxies manuais
# que precisavam ser mantidos em sincronia com o QuerySet.
AtendimentoManager = models.Manager.from_queryset(AtendimentoQuerySet)


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

    # ───────── FSM transitions (campo continua CharField) ─────────
    # TODA mudanca de status em views/services/admin passa por estes metodos
    # (aprovar/confirmar/cancelar/marcar_realizado/marcar_falta/
    # marcar_reagendado) dentro de try/except Atendimento.TransicaoInvalida.
    # `status = X; save()` pula validacao, auditoria e os eventos de dominio
    # (comissao, cashback, retorno, lista de espera).

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
        # Valida contra o status ATUAL do banco com a linha travada
        # (select_for_update): duplo clique / link da cliente + recepcao ao
        # mesmo tempo nao passam os dois pela checagem nem publicam o mesmo
        # evento 2x. Mudanca de status + auditoria sao atomicas entre si; os
        # efeitos de _publish_event (chamados pelos metodos publicos APOS o
        # _transicionar) sao best-effort.
        with transaction.atomic():
            if self.pk is not None:
                atual = (
                    type(self).objects.select_for_update()
                    .values_list('status', flat=True).get(pk=self.pk)
                )
                # memoria reflete o banco (um save() posterior nao grava status velho)
                self.status = atual
            else:
                atual = self.status
            permitido = self.TRANSICOES.get(atual, set())
            if novo_status not in permitido:
                raise self.TransicaoInvalida(
                    f'Transição {atual} → {novo_status} não permitida'
                )
            anterior = atual
            self.status = novo_status
            self.save(update_fields=['status', 'atualizado_em'])
            try:
                from .sistema import LogAuditoria
                # atomic aninhado (savepoint): falha de auditoria nao "envenena"
                # a transacao externa nem desfaz a mudanca de status.
                with transaction.atomic():
                    LogAuditoria.objects.create(
                        usuario=by_user,
                        acao=f'Atendimento {self.pk}: {anterior} -> {novo_status}'
                             + (f' ({motivo})' if motivo else ''),
                        tabela='atendimento',
                        registro_id=self.pk,
                    )
            except (DatabaseError, IntegrityError) as exc:
                # Auditoria best-effort — falha de DB nao bloqueia transicao de status
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
            from ..domain import event_bus, events as domain_events
            event_cls = getattr(domain_events, event_name, None)
            if not event_cls:
                return
            event_bus.EventBus.publish(event_cls(
                occurred_at=timezone.now(),
                atendimento_id=self.pk,
                **fields,
            ))
        except Exception as exc:  # pylint: disable=broad-except
            # Bus best-effort — mantem o fluxo, mas deixa rastro para diagnostico
            # (caso contrario efeitos como comissao/cashback/notificacao somem em silencio).
            logging.getLogger(__name__).warning(
                'event_publish_falhou',
                extra={'event': event_name, 'atendimento_id': self.pk, 'erro': str(exc)},
            )

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
            # F-RET: no maximo 1 retorno vivo (ou ja realizado) por atendimento de
            # origem — garante no banco a idempotencia do RetornoService.
            models.UniqueConstraint(
                fields=['atendimento_origem'],
                condition=models.Q(
                    eh_retorno=True,
                    status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO', 'REALIZADO'],
                ),
                name='uniq_retorno_por_origem',
            ),
            # PG: excl_atendimento_sobreposicao (EXCLUDE gist, migration 0035)
            # impede 2 atendimentos ativos sobrepostos do mesmo profissional.
        ]

    def clean(self):
        # Espelha o EXCLUDE do PG p/ ModelForm/admin: erro de formulario em vez
        # de IntegrityError (500). So roda em full_clean (booking/services nao).
        super().clean()
        if not (self.data_hora_inicio and self.data_hora_fim):
            return
        if self.data_hora_fim <= self.data_hora_inicio:
            raise ValidationError({'data_hora_fim': 'O fim deve ser depois do início.'})
        if self.profissional_id and self.status in self.STATUS_ATIVOS:
            conflito = (
                Atendimento.objects
                .conflito_com(self.profissional_id, self.data_hora_inicio, self.data_hora_fim)
                .exclude(pk=self.pk)
                .exists()
            )
            if conflito:
                raise ValidationError(
                    'Conflito com outro atendimento ativo deste profissional nesse horário.'
                )

    def __str__(self):
        data_fmt = fmt_local(self.data_hora_inicio) or 's/ data'
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
        # Link de assinatura do termo (/termo/<token>/) — nunca vale em /confirmar/
        ('TERMO', 'Termo de consentimento'),
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
        data_fmt = fmt_local(self.criado_em) or 's/ data'
        cliente_nome = (
            self.atendimento.cliente.nome
            if self.atendimento_id and self.atendimento.cliente_id
            else 's/ cliente'
        )
        return f'{self.get_tipo_display()} — {cliente_nome} ({data_fmt})'
