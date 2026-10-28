# aranha_estetica/models/agendamentos.py — Atendimentos e notificacoes
import logging

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import DatabaseError, IntegrityError, models, transaction
from django.db.models.functions import Now
from django.utils import timezone

from ..db.indices import Indice
from ..utils.datas import fmt_local
from .clientes import Cliente
from .mixins import ATUALIZADO_EM_BACKFILL, atualizado_em_legado
from .procedimentos import Procedimento, Promocao
from .profissionais import Profissional


class StatusAtendimento(models.TextChoices):
    """Status do atendimento (FSM em Atendimento.TRANSICOES). Membro e str: compara com o valor do
    banco; erro de digitacao vira AttributeError em vez de filtro vazio."""
    PENDENTE = 'PENDENTE', 'Pendente de Confirmação'
    AGENDADO = 'AGENDADO', 'Agendado'
    CONFIRMADO = 'CONFIRMADO', 'Confirmado'
    REALIZADO = 'REALIZADO', 'Realizado'
    CANCELADO = 'CANCELADO', 'Cancelado'
    FALTOU = 'FALTOU', 'Faltou'
    REAGENDADO = 'REAGENDADO', 'Reagendado'


# Grupos usados em consulta (ocupam a agenda / encerrados)
STATUS_ATIVOS = (StatusAtendimento.PENDENTE, StatusAtendimento.AGENDADO, StatusAtendimento.CONFIRMADO)
STATUS_FINALIZADOS = (
    StatusAtendimento.REALIZADO, StatusAtendimento.CANCELADO, StatusAtendimento.FALTOU,
    StatusAtendimento.REAGENDADO,
)


class TipoNotificacao(models.TextChoices):
    LEMBRETE = 'LEMBRETE', 'Lembrete D-1'
    LEMBRETE_2H = 'LEMBRETE_2H', 'Lembrete T-2h'
    CONFIRMACAO = 'CONFIRMACAO', 'Confirmação'
    CANCELAMENTO = 'CANCELAMENTO', 'Cancelamento'
    NPS = 'NPS', 'Pesquisa NPS'
    PESQUISA = 'PESQUISA', 'Pesquisa de satisfação detalhada'
    APROVACAO = 'APROVACAO', 'Aprovação Profissional'
    # Link de assinatura do termo (/termo/<token>, LinkAcesso TERMO)
    TERMO = 'TERMO', 'Termo de consentimento'


class CanalNotificacao(models.TextChoices):
    WHATSAPP = 'WHATSAPP', 'WhatsApp'
    SMS = 'SMS', 'SMS'
    EMAIL = 'EMAIL', 'E-mail'


class StatusNotificacao(models.TextChoices):
    PENDENTE = 'PENDENTE', 'Pendente'
    ENVIADO = 'ENVIADO', 'Enviado'
    FALHOU = 'FALHOU', 'Falhou'


class RespostaNotificacao(models.TextChoices):
    CONFIRMOU = 'CONFIRMOU', 'Confirmou'
    CANCELOU = 'CANCELOU', 'Cancelou'


class AtendimentoQuerySet(models.QuerySet):
    """QuerySet com queries reutilizaveis — encapsula conhecimento de dominio."""

    def ativos(self):
        """Atendimentos nao cancelados/reagendados/faltados."""
        return self.exclude(status__in=[
            StatusAtendimento.CANCELADO, StatusAtendimento.REAGENDADO, StatusAtendimento.FALTOU,
        ])

    def futuros(self):
        return self.filter(data_hora_inicio__gte=timezone.now())

    def passados(self):
        return self.filter(data_hora_fim__lt=timezone.now())

    def hoje(self):
        hoje = timezone.localdate()
        return self.filter(data_hora_inicio__date=hoje)

    def pendentes_aprovacao(self):
        return self.filter(status=StatusAtendimento.PENDENTE)

    def realizados(self):
        return self.filter(status=StatusAtendimento.REALIZADO)

    def conflito_com(self, profissional, data_inicio, data_fim):
        """Atendimentos que conflitam com janela [data_inicio, data_fim)."""
        return self.filter(
            profissional=profissional,
            data_hora_inicio__lt=data_fim,
            data_hora_fim__gt=data_inicio,
            status__in=STATUS_ATIVOS,
        )


# from_queryset expoe automaticamente todos os metodos do QuerySet no manager
# (ativos/futuros/passados/hoje/conflito_com/...), eliminando os proxies manuais
# que precisavam ser mantidos em sincronia com o QuerySet.
AtendimentoManager = models.Manager.from_queryset(AtendimentoQuerySet)


class Atendimento(models.Model):
    # Atalhos do enum StatusAtendimento (codigo antigo usa Atendimento.STATUS_*)
    Status = StatusAtendimento
    STATUS_PENDENTE = StatusAtendimento.PENDENTE
    STATUS_AGENDADO = StatusAtendimento.AGENDADO
    STATUS_CONFIRMADO = StatusAtendimento.CONFIRMADO
    STATUS_REALIZADO = StatusAtendimento.REALIZADO
    STATUS_CANCELADO = StatusAtendimento.CANCELADO
    STATUS_FALTOU = StatusAtendimento.FALTOU
    STATUS_REAGENDADO = StatusAtendimento.REAGENDADO

    # Grupos para queries semanticas
    STATUS_FINALIZADOS = STATUS_FINALIZADOS
    STATUS_ATIVOS = STATUS_ATIVOS

    STATUS_CHOICES = StatusAtendimento.choices

    # FKs sem indice automatico: os ix_ do Meta cobrem (spec §2.4/§2.11)
    cliente = models.ForeignKey(Cliente, on_delete=models.RESTRICT, db_index=False)
    profissional = models.ForeignKey(Profissional, on_delete=models.RESTRICT, db_index=False)
    procedimento = models.ForeignKey(Procedimento, on_delete=models.RESTRICT, db_index=False)
    promocao = models.ForeignKey(
        Promocao, on_delete=models.SET_NULL, blank=True, null=True, db_index=False,
    )
    # aponta p/ o atendimento ANTIGO (status REAGENDADO) que este substitui
    reagendado_de = models.ForeignKey(
        'self', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='reagendamentos', db_column='atendimento_reagendado_id', db_index=False,
        db_comment='Atendimento anterior (status REAGENDADO) que este substitui.',
    )
    # F-RET — vinculo entre sessao principal e retorno gratuito
    atendimento_origem = models.ForeignKey(
        'self', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='retornos', db_index=False,
        db_comment='Sessao principal quando este e o retorno gratuito (eh_retorno).',
    )
    eh_retorno = models.BooleanField(default=False, db_default=False)
    data_hora_inicio = models.DateTimeField()
    data_hora_fim = models.DateTimeField()
    valor_cobrado = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        validators=[MinValueValidator(0)],
        db_comment=(
            'Preco gravado no agendamento (promocao da data); sessao de pacote leva o preco cheio e nao '
            'soma no faturamento (D20).'
        ),
    )
    valor_original = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        validators=[MinValueValidator(0)],
        db_comment='Preco cheio quando houve promocao.',
    )
    descricao_preco = models.TextField(blank=True, default='', db_default='')
    status = models.CharField(
        max_length=20, default=StatusAtendimento.PENDENTE, db_default=StatusAtendimento.PENDENTE,
        choices=StatusAtendimento.choices,
    )
    # Link publico de reagendar/cancelar: link_acesso (GERENCIAR_AGENDAMENTO, so
    # os migrados da 0049). O portal "Meus agendamentos" usa id + sessao do OTP.
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    atualizado_em = models.DateTimeField(auto_now=True, db_default=Now(), db_comment=ATUALIZADO_EM_BACKFILL)

    # ───────── FSM transitions (campo continua CharField) ─────────
    # TODA mudanca de status em views/services/admin passa por estes metodos
    # (aprovar/confirmar/cancelar/marcar_realizado/marcar_falta/
    # marcar_reagendado) dentro de try/except Atendimento.TransicaoInvalida.
    # `status = X; save()` pula validacao, auditoria e os eventos de dominio
    # (comissao, cashback, retorno, lista de espera).

    TRANSICOES = {
        StatusAtendimento.PENDENTE: {
            StatusAtendimento.AGENDADO, StatusAtendimento.CONFIRMADO, StatusAtendimento.CANCELADO,
            StatusAtendimento.REAGENDADO,
        },
        StatusAtendimento.AGENDADO: {
            StatusAtendimento.CONFIRMADO, StatusAtendimento.CANCELADO, StatusAtendimento.FALTOU,
            StatusAtendimento.REAGENDADO, StatusAtendimento.REALIZADO,
        },
        StatusAtendimento.CONFIRMADO: {
            StatusAtendimento.REALIZADO, StatusAtendimento.CANCELADO, StatusAtendimento.FALTOU,
            StatusAtendimento.REAGENDADO,
        },
        StatusAtendimento.REALIZADO: set(),
        StatusAtendimento.CANCELADO: set(),
        StatusAtendimento.FALTOU: set(),
        StatusAtendimento.REAGENDADO: set(),
    }

    class TransicaoInvalida(Exception):
        pass

    def _transicionar(self, novo_status, motivo=None, by_user=None):
        # Valida contra o status ATUAL do banco com a linha travada
        # (select_for_update): duplo clique / link da cliente + recepcao ao
        # mesmo tempo nao passam os dois pela checagem nem publicam o mesmo
        # evento 2x. Os metodos publicos rodam este passo E o _publish_event na
        # MESMA transacao: comissao, cashback e retorno gravam junto com o status
        # ou nada grava (I/O externo dos handlers vai em on_commit).
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
        from ..domain.events import AtendimentoConfirmado
        with transaction.atomic():
            self._transicionar(StatusAtendimento.CONFIRMADO, by_user=by_user)
            self._publish_event(AtendimentoConfirmado, confirmado_por_id=getattr(by_user, 'pk', None))

    def cancelar(self, motivo='', by_user=None, *, pela_cliente=False):
        """pela_cliente: so os fluxos da propria cliente (link/portal) passam True — job e
        equipe nao (antes `by_user is None` marcava o cancelamento automatico como da cliente)."""
        from ..domain.events import AtendimentoCancelado
        with transaction.atomic():
            self._transicionar(StatusAtendimento.CANCELADO, motivo=motivo, by_user=by_user)
            self._publish_event(
                AtendimentoCancelado,
                motivo=motivo or '',
                cancelado_por_cliente=pela_cliente,
            )

    def marcar_realizado(self, by_user=None):
        from ..domain.events import AtendimentoRealizado
        with transaction.atomic():
            self._transicionar(StatusAtendimento.REALIZADO, by_user=by_user)
            self._publish_event(
                AtendimentoRealizado,
                cliente_id=self.cliente_id,
                profissional_id=self.profissional_id,
            )

    def marcar_falta(self, by_user=None):
        from ..domain.events import AtendimentoFaltou
        with transaction.atomic():
            self._transicionar(StatusAtendimento.FALTOU, by_user=by_user)
            self._publish_event(AtendimentoFaltou, cliente_id=self.cliente_id)

    def marcar_reagendado(self, by_user=None):
        self._transicionar(StatusAtendimento.REAGENDADO, by_user=by_user)

    def aprovar(self, by_user=None):
        self._transicionar(StatusAtendimento.AGENDADO, by_user=by_user)

    def _publish_event(self, event_cls, **fields) -> None:
        """Publica o DomainEvent (classe, nao nome: erro de digitacao vira ImportError) na
        transacao da transicao. Handler que falha propaga e desfaz a transicao inteira — antes a
        falha de comissao/cashback/retorno virava so uma linha de log com o status ja gravado."""
        from ..domain.event_bus import EventBus
        EventBus.publish(event_cls(occurred_at=timezone.now(), atendimento_id=self.pk, **fields))

    objects = AtendimentoManager()

    class Meta:
        managed = True
        db_table = 'atendimento'
        db_table_comment = (
            'Agendamento/sessao. FSM (models.Atendimento.TRANSICOES): PENDENTE->AGENDADO->CONFIRMADO->'
            'REALIZADO|CANCELADO|FALTOU|REAGENDADO. ex_atendimento_sobreposicao impede 2 ativos sobrepostos '
            'do mesmo profissional.'
        )
        indexes = [
            # slots/conflito/agenda por profissional + dia (cobre a FK profissional)
            Indice(fields=['profissional', 'data_hora_inicio'], name='ix_atendimento_profissional_data_hora_inicio'),
            # historico do cliente ordenado (cobre a FK cliente)
            Indice(fields=['cliente', 'data_hora_inicio'], name='ix_atendimento_cliente_data_hora_inicio'),
            Indice(fields=['data_hora_inicio'], name='ix_atendimento_data_hora_inicio'),
            # "agenda em aberto" (proximos/pendentes)
            Indice(
                fields=['data_hora_inicio'],
                condition=models.Q(status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']),  # = STATUS_ATIVOS
                name='ix_atendimento_data_hora_inicio_ativo_parcial',
            ),
            Indice(fields=['procedimento'], name='ix_atendimento_procedimento'),
            Indice(
                fields=['promocao'], condition=models.Q(promocao__isnull=False),
                name='ix_atendimento_promocao_parcial',
            ),
            Indice(
                fields=['reagendado_de'], condition=models.Q(reagendado_de__isnull=False),
                name='ix_atendimento_atendimento_reagendado_parcial',
            ),
            Indice(
                fields=['atendimento_origem'], condition=models.Q(atendimento_origem__isnull=False),
                name='ix_atendimento_atendimento_origem_parcial',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=StatusAtendimento.values),   # CHECK = o enum
                name='ck_atendimento_status'
            ),
            models.CheckConstraint(
                condition=models.Q(data_hora_fim__gt=models.F('data_hora_inicio')),
                name='ck_atendimento_fim_apos_inicio',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(eh_retorno=False) | models.Q(atendimento_origem__isnull=False)
                ),
                name='ck_atendimento_retorno_tem_origem',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(eh_retorno=False) | models.Q(valor_cobrado=0) |
                    models.Q(valor_cobrado__isnull=True)
                ),
                name='ck_atendimento_retorno_valor_zero',
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(valor_cobrado__isnull=True) | models.Q(valor_cobrado__gte=0))
                    & (models.Q(valor_original__isnull=True) | models.Q(valor_original__gte=0))
                ),
                name='ck_atendimento_valores_nao_negativos',
            ),
            # F-RET: no maximo 1 retorno vivo (ou ja realizado) por atendimento de
            # origem — garante no banco a idempotencia do RetornoService.
            models.UniqueConstraint(
                fields=['atendimento_origem'],
                condition=models.Q(
                    eh_retorno=True,
                    status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO', 'REALIZADO'],
                ),
                name='uq_atendimento_retorno_por_origem',
            ),
            # PG: ex_atendimento_sobreposicao (EXCLUDE gist, 0035; nome desde a 0055)
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
    Tipo = TipoNotificacao
    Canal = CanalNotificacao
    Status = StatusNotificacao
    Resposta = RespostaNotificacao
    TIPO_CHOICES = TipoNotificacao.choices
    CANAL_CHOICES = CanalNotificacao.choices
    STATUS_CHOICES = StatusNotificacao.choices
    RESPOSTA_CHOICES = RespostaNotificacao.choices

    # FK sem indice automatico: coberta pelo ix_notificacao_atendimento_tipo
    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE, db_index=False)
    tipo = models.CharField(
        max_length=30, default=TipoNotificacao.LEMBRETE, db_default=TipoNotificacao.LEMBRETE,
        choices=TipoNotificacao.choices,
    )
    canal = models.CharField(
        max_length=20, default=CanalNotificacao.WHATSAPP, db_default=CanalNotificacao.WHATSAPP,
        choices=CanalNotificacao.choices,
    )
    status = models.CharField(
        max_length=20, default=StatusNotificacao.PENDENTE, db_default=StatusNotificacao.PENDENTE,
        choices=StatusNotificacao.choices,
    )
    resposta = models.CharField(
        max_length=20, blank=True, null=True, choices=RespostaNotificacao.choices
    )
    # O link publico da mensagem (/confirmar/, /nps/, /termo/) vive em
    # link_acesso (so o hash do token) — services/links.py.
    mensagem = models.TextField(blank=True, default='', db_default='')
    enviado_em = models.DateTimeField(blank=True, null=True)
    respondido_em = models.DateTimeField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'notificacao'
        db_table_comment = (
            'Mensagem ligada a um atendimento (lembrete, NPS, termo...). O link publico da mensagem vive em '
            'link_acesso (so o hash). mensagem e limpa apos 12 meses.'
        )
        indexes = [
            Indice(fields=['atendimento', 'tipo'], name='ix_notificacao_atendimento_tipo'),
            Indice(fields=['-criado_em'], name='ix_notificacao_criado_em'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(tipo__in=TipoNotificacao.values), name='ck_notificacao_tipo',
            ),
            models.CheckConstraint(condition=models.Q(canal__in=CanalNotificacao.values), name='ck_notificacao_canal'),
            models.CheckConstraint(
                condition=models.Q(status__in=StatusNotificacao.values), name='ck_notificacao_status',
            ),
            models.CheckConstraint(
                condition=models.Q(resposta__isnull=True) | models.Q(resposta__in=RespostaNotificacao.values),
                name='ck_notificacao_resposta',
            ),
        ]

    def __str__(self):
        data_fmt = fmt_local(self.criado_em) or 's/ data'
        cliente_nome = (
            self.atendimento.cliente.nome
            if self.atendimento_id and self.atendimento.cliente_id
            else 's/ cliente'
        )
        return f'{self.get_tipo_display()} — {cliente_nome} ({data_fmt})'
