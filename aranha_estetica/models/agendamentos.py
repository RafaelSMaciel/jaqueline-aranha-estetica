# aranha_estetica/models/agendamentos.py — Atendimento (FSM); Notificacao em notificacoes.py

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models.functions import Now
from django.utils import timezone

from ..db.indices import Indice
from ..constants import TOLERANCIA_DESFECHO
from ..excecoes import TransicaoInvalida
from ..utils.datas import dias_locais, fmt_local
from .clientes import Cliente
from .mixins import ATUALIZADO_EM_BACKFILL
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
        return self.filter(**dias_locais('data_hora_inicio', timezone.localdate()))

    def pendentes_aprovacao(self):
        return self.filter(status=StatusAtendimento.PENDENTE)

    def sem_desfecho(self, ate=None):
        """Ja terminaram e seguem AGENDADO/CONFIRMADO: ninguem marcou Realizado, Faltou ou Cancelado
        (sem desfecho nao gera comissao, nao debita pacote, nao dispara NPS nem entra no faturamento)."""
        return self.filter(
            data_hora_fim__lt=ate or timezone.now(),
            status__in=(StatusAtendimento.AGENDADO, StatusAtendimento.CONFIRMADO),
        )

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
    # Reagendar/cancelar pela cliente: portal "Meus agendamentos" (id + sessao do OTP), sem token.
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    atualizado_em = models.DateTimeField(auto_now=True, db_default=Now(), db_comment=ATUALIZADO_EM_BACKFILL)

    # ───────── FSM transitions (campo continua CharField) ─────────
    # TODA mudanca de status em views/services/admin passa por estes metodos
    # (aprovar/confirmar/cancelar/marcar_realizado/marcar_falta/
    # marcar_reagendado) dentro de try/except Atendimento.TransicaoInvalida.
    # `status = X; save()` pula validacao, auditoria e os eventos de dominio
    # (comissao, cashback, retorno, lista de espera).

    TRANSICOES = {
        # Pedido so sai aprovado (AGENDADO), recusado/vencido (CANCELADO) ou remarcado: confirmar
        # presenca de algo que a clinica nao aprovou pulava o e-mail de confirmacao a cliente.
        StatusAtendimento.PENDENTE: {
            StatusAtendimento.AGENDADO, StatusAtendimento.CANCELADO, StatusAtendimento.REAGENDADO,
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

    # Realizado e terminal na FSM comum; so o "Desfazer realizacao" do painel (admin, com motivo)
    # sai dele — os efeitos (comissao, cashback, sessao de pacote, retorno) sao desfeitos juntos.
    DESFAZER_REALIZACAO = {StatusAtendimento.AGENDADO, StatusAtendimento.CANCELADO, StatusAtendimento.FALTOU}

    # Atendimento.TransicaoInvalida segue valendo (views/admin/tasks); e a RegraDeNegocio comum.
    TransicaoInvalida = TransicaoInvalida

    def _transicionar(self, novo_status, motivo=None, by_user=None, *, pela_cliente=False, desfazendo=False):
        # Valida contra o status ATUAL do banco com a linha travada
        # (select_for_update): duplo clique / link da cliente + recepcao ao
        # mesmo tempo nao passam os dois pela checagem nem aplicam os efeitos 2x.
        # Status, auditoria e efeitos (services.atendimento.ao_mudar_status: faltas,
        # pacote, comissao, cashback, retorno, lista de espera) na MESMA transacao:
        # efeito que falha desfaz a transicao inteira (I/O externo vai em on_commit).
        from ..services.atendimento import ao_mudar_status
        from ..utils.auditoria import registrar_log
        with transaction.atomic():
            if self.pk is not None:
                atual, inicio = (
                    type(self).objects.select_for_update()
                    .values_list('status', 'data_hora_inicio').get(pk=self.pk)
                )
                # memoria reflete o banco (um save() posterior nao grava status velho)
                self.status = atual
            else:
                atual, inicio = self.status, self.data_hora_inicio
            if (novo_status in (StatusAtendimento.REALIZADO, StatusAtendimento.FALTOU) and inicio
                    and inicio > timezone.now() + TOLERANCIA_DESFECHO):
                raise self.TransicaoInvalida(
                    'Só é possível marcar realizado ou falta depois do início do atendimento.',
                    codigo='desfecho_antes_do_inicio')
            permitido = (self.DESFAZER_REALIZACAO if desfazendo and atual == StatusAtendimento.REALIZADO
                         else self.TRANSICOES.get(atual, set()))
            if novo_status not in permitido:
                raise self.TransicaoInvalida(
                    f'Não é possível passar de {StatusAtendimento(atual).label.lower()} para '
                    f'{StatusAtendimento(novo_status).label.lower()}.'
                )
            anterior = atual
            self.status = novo_status
            self.save(update_fields=['status', 'atualizado_em'])
            # autor = by_user ou o Ator da borda (com IP); best-effort, em savepoint
            registrar_log(
                by_user,
                f'Atendimento {self.pk}: {anterior} -> {novo_status}'
                + (f' ({motivo})' if motivo else '') + (' [pela cliente]' if pela_cliente else ''),
                'atendimento', self.pk,
            )
            ao_mudar_status(self, anterior, novo_status, pela_cliente=pela_cliente)

    def confirmar(self, by_user=None):
        self._transicionar(StatusAtendimento.CONFIRMADO, by_user=by_user)

    def cancelar(self, motivo='', by_user=None, *, pela_cliente=False):
        """pela_cliente: so os fluxos da propria cliente (link/portal) passam True — job e
        equipe nao; fica na trilha (LogAuditoria) e a clinica nao avisa a cliente dela mesma."""
        self._transicionar(StatusAtendimento.CANCELADO, motivo=motivo, by_user=by_user, pela_cliente=pela_cliente)

    def marcar_realizado(self, by_user=None):
        self._transicionar(StatusAtendimento.REALIZADO, by_user=by_user)

    def marcar_falta(self, by_user=None):
        self._transicionar(StatusAtendimento.FALTOU, by_user=by_user)

    def marcar_reagendado(self, by_user=None, *, pela_cliente=False):
        self._transicionar(StatusAtendimento.REAGENDADO, by_user=by_user, pela_cliente=pela_cliente)

    def aprovar(self, by_user=None):
        self._transicionar(StatusAtendimento.AGENDADO, by_user=by_user)

    def desfazer_realizacao(self, destino, motivo, by_user=None):
        """REALIZADO marcado por engano -> AGENDADO (vai acontecer), FALTOU ou CANCELADO. Estorna a
        comissao pendente (paga recusa), o cashback, devolve a sessao do pacote e cancela o retorno
        sugerido (services/atendimento). Motivo obrigatorio (fica na trilha)."""
        if not (motivo or '').strip():
            raise self.TransicaoInvalida('Informe o motivo para desfazer a realização.')
        self._transicionar(destino, motivo=f'realização desfeita: {motivo.strip()}', by_user=by_user,
                           desfazendo=True)

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
                condition=models.Q(status__in=STATUS_ATIVOS),
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
                    status__in=(*STATUS_ATIVOS, StatusAtendimento.REALIZADO),
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
