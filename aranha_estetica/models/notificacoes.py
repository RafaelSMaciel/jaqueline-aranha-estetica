"""Notificacao: toda mensagem ligada a um atendimento (lembrete, NPS, termo, e-mails) — o historico
por canal e a fila do e-mail (services/comunicacao)."""
from django.db import models
from django.db.models.functions import Now

from ..db.indices import Indice
from ..utils.datas import fmt_local
from .agendamentos import Atendimento, StatusAtendimento
from .mixins import atualizado_em_legado


class TipoNotificacao(models.TextChoices):
    LEMBRETE = 'LEMBRETE', 'Lembrete D-1'
    CONFIRMACAO = 'CONFIRMACAO', 'Confirmação'
    CANCELAMENTO = 'CANCELAMENTO', 'Cancelamento'
    NPS = 'NPS', 'Pesquisa NPS'
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
    # Fila de e-mail (services/comunicacao): PENDENTE com proxima_tentativa_em vencida = o cron
    # 'emails' reenvia; esgotou as tentativas ou a validade -> FALHOU.
    tentativas = models.SmallIntegerField(
        default=0, db_default=0, db_comment='Envios que falharam (fila de e-mail: services/comunicacao).',
    )
    proxima_tentativa_em = models.DateTimeField(
        blank=True, null=True,
        db_comment='Fila de e-mail: quando o cron reenvia (NULL = fora da fila).',
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'notificacao'
        db_table_comment = (
            'Mensagem ligada a um atendimento (lembrete, NPS, termo, e-mails de confirmacao/cancelamento/'
            'aprovacao): historico por canal e fila do e-mail (tentativas/proxima_tentativa_em). O link '
            'publico da mensagem vive em link_acesso (so o hash). mensagem e limpa apos 12 meses.'
        )
        indexes = [
            Indice(fields=['atendimento', 'tipo'], name='ix_notificacao_atendimento_tipo'),
            Indice(fields=['-criado_em'], name='ix_notificacao_criado_em'),
            Indice(
                fields=['proxima_tentativa_em'], name='ix_notificacao_fila_email',
                condition=models.Q(status=StatusNotificacao.PENDENTE, canal=CanalNotificacao.EMAIL),
            ),
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
            models.CheckConstraint(condition=models.Q(tentativas__gte=0), name='ck_notificacao_tentativas'),
        ]

    def __str__(self):
        data_fmt = fmt_local(self.criado_em) or 's/ data'
        cliente_nome = (
            self.atendimento.cliente.nome
            if self.atendimento_id and self.atendimento.cliente_id
            else 's/ cliente'
        )
        return f'{self.get_tipo_display()} — {cliente_nome} ({data_fmt})'


# Notificacoes que pedem resposta pelo link /confirmar/ (LinkAcesso CONFIRMAR_PRESENCA, emitido no
# lembrete por WhatsApp). Link de termo e de NPS nao valem la (finalidade diferente -> 404).
TIPOS_LINK_CONFIRMACAO = (TipoNotificacao.LEMBRETE, TipoNotificacao.CONFIRMACAO)
# Pelo link a cliente so mexe em agendamento ja aprovado (PENDENTE segue p/ a profissional).
STATUS_ALTERAVEIS_PELO_LINK = (StatusAtendimento.AGENDADO, StatusAtendimento.CONFIRMADO)
# "Sem resposta" so vale p/ o que pede resposta (link de confirmacao enviado): termo, NPS,
# cancelamento e falha de envio nao contam.
AGUARDANDO_RESPOSTA = models.Q(
    tipo__in=TIPOS_LINK_CONFIRMACAO, canal=CanalNotificacao.WHATSAPP, status=StatusNotificacao.ENVIADO,
    resposta__isnull=True,
)
