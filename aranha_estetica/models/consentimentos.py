# aranha_estetica/models/consentimentos.py — Consentimento de comunicacao com historico
"""Consentimento (docs/specs/padrao-sql.md §2.7 e §4.c — migration 0050).

Historico append-only de concessao/revogacao por finalidade x canal (LGPD
art. 8): cada mudanca e uma linha nova; o estado atual e a linha mais recente
do par (``registrado_em DESC, id DESC``). Sem linha = nao concedido. Revogar =
novo registro com ``concedido=False`` (nunca UPDATE/DELETE). Registrar, ler o
estado e anotar querysets: ``services/consentimentos.py``.

Unica mutacao aceita: a minimizacao LGPD da anonimizacao, que apaga
``ip_origem``/``user_agent`` das linhas do titular (o historico — datas,
finalidades, origem — fica como prova de que houve base legal).

So-PG (migration 0050): ``tg_consentimento_imutavel`` (o Python cobre com
save()/delete() que recusam).
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now
from django.utils import timezone

from ..db.indices import Indice

MSG_IMUTAVEL = 'Consentimento é histórico append-only: registre uma nova linha (conceder/revogar).'


class ConsentimentoQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError(MSG_IMUTAVEL)

    delete.alters_data = True
    delete.queryset_only = True

    def do_titular(self, cliente):
        return self.filter(cliente_id=getattr(cliente, 'pk', cliente))


class Consentimento(models.Model):
    # finalidade
    MARKETING = 'MARKETING'
    PESQUISA_SATISFACAO = 'PESQUISA_SATISFACAO'
    LEMBRETE_AGENDAMENTO = 'LEMBRETE_AGENDAMENTO'
    FINALIDADE_CHOICES = [
        (MARKETING, 'Promoções e novidades'),
        (PESQUISA_SATISFACAO, 'Pesquisa de satisfação'),
        (LEMBRETE_AGENDAMENTO, 'Lembrete do agendamento'),
    ]
    # canal
    EMAIL = 'EMAIL'
    WHATSAPP = 'WHATSAPP'
    SMS = 'SMS'
    CANAL_CHOICES = [(EMAIL, 'E-mail'), (WHATSAPP, 'WhatsApp'), (SMS, 'SMS')]
    # pares que existem (ck_consentimento_finalidade_canal): um par novo = migration
    PARES = (
        (MARKETING, EMAIL),
        (PESQUISA_SATISFACAO, WHATSAPP),
        (LEMBRETE_AGENDAMENTO, WHATSAPP),
    )
    # origem
    ORIGEM_AGENDAMENTO_ONLINE = 'AGENDAMENTO_ONLINE'
    ORIGEM_DESCADASTRO = 'DESCADASTRO'
    ORIGEM_PAINEL = 'PAINEL'
    ORIGEM_ESQUECIMENTO = 'ESQUECIMENTO'
    ORIGEM_MIGRACAO = 'MIGRACAO'
    ORIGEM_ADMIN = 'ADMIN'
    ORIGEM_CHOICES = [
        (ORIGEM_AGENDAMENTO_ONLINE, 'Agendamento online (a própria cliente)'),
        (ORIGEM_DESCADASTRO, 'Link de descadastro'),
        (ORIGEM_PAINEL, 'Painel (equipe)'),
        (ORIGEM_ESQUECIMENTO, 'Direito ao esquecimento'),
        (ORIGEM_MIGRACAO, 'Migração do cadastro antigo'),
        (ORIGEM_ADMIN, 'Django admin'),
    ]

    # FK sem indice automatico: coberta pelo ix_consentimento_cliente_finalidade_canal_registrado_em
    cliente = models.ForeignKey(
        'Cliente', on_delete=models.PROTECT, related_name='consentimentos', db_index=False,
    )
    finalidade = models.CharField(max_length=30, choices=FINALIDADE_CHOICES)
    canal = models.CharField(max_length=20, choices=CANAL_CHOICES)
    concedido = models.BooleanField(db_column='eh_concedido')
    origem = models.CharField(max_length=30, choices=ORIGEM_CHOICES)
    texto = models.TextField(
        blank=True, default='', db_default='',
        db_comment='Texto exibido ao titular no momento (prova do que foi consentido).',
    )
    ip_origem = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=500, blank=True, default='', db_default='')
    # PROTECT (= NO ACTION): o trigger nao deixa a autoria mudar depois
    usuario_autor = models.ForeignKey(
        'Usuario', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        db_index=False,
    )
    registrado_em = models.DateTimeField(
        default=timezone.now, db_default=Now(),
        db_comment='Quando o titular concedeu/revogou (na migracao = data original de consent_*_em).',
    )
    criado_em = models.DateTimeField(default=timezone.now, db_default=Now(), editable=False)

    objects = ConsentimentoQuerySet.as_manager()

    class Meta:
        db_table = 'consentimento'
        db_table_comment = (
            'Historico append-only de concessao/revogacao por finalidade x canal (LGPD art. 8). '
            'Estado atual = linha mais recente (vw_consentimento_atual / fn_tem_consentimento). '
            'Sem linha = nao concedido.'
        )
        constraints = [
            models.CheckConstraint(
                condition=Q(finalidade__in=['MARKETING', 'PESQUISA_SATISFACAO', 'LEMBRETE_AGENDAMENTO']),
                name='ck_consentimento_finalidade',
            ),
            models.CheckConstraint(
                condition=Q(canal__in=['EMAIL', 'WHATSAPP', 'SMS']),
                name='ck_consentimento_canal',
            ),
            models.CheckConstraint(
                condition=(
                    Q(finalidade='MARKETING', canal='EMAIL')
                    | Q(finalidade='PESQUISA_SATISFACAO', canal='WHATSAPP')
                    | Q(finalidade='LEMBRETE_AGENDAMENTO', canal='WHATSAPP')
                ),
                name='ck_consentimento_finalidade_canal',
            ),
            models.CheckConstraint(
                condition=Q(origem__in=[
                    'AGENDAMENTO_ONLINE', 'DESCADASTRO', 'PAINEL', 'ESQUECIMENTO', 'MIGRACAO', 'ADMIN',
                ]),
                name='ck_consentimento_origem',
            ),
        ]
        indexes = [
            Indice(
                fields=['cliente', 'finalidade', 'canal', '-registrado_em', '-id'],
                name='ix_consentimento_cliente_finalidade_canal_registrado_em',
            ),
            Indice(
                fields=['usuario_autor'], condition=Q(usuario_autor__isnull=False),
                name='ix_consentimento_usuario_autor_parcial',
            ),
        ]

    def __str__(self):
        acao = 'concedeu' if self.concedido else 'revogou'
        return f'Cliente {self.cliente_id} {acao} {self.finalidade}/{self.canal}'

    @property
    def par(self):
        return (self.finalidade, self.canal)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(MSG_IMUTAVEL)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(MSG_IMUTAVEL)
