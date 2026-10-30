# aranha_estetica/models/links.py — Links publicos com token (link magico)
"""LinkAcesso (docs/specs/padrao-sql.md §2.7 e §4.b — migration 0049).

Toda URL publica autenticada por token (/confirmar/, /nps/, /termo/,
/anamnese/, /pesquisa/, /lgpd/unsubscribe/, feed ICS) vira uma linha aqui. So o SHA-256 do token e gravado (``token_hash``): o token em
claro existe apenas na URL entregue — emitir/resolver/revogar em
``services/links.py``. O alvo e tipado pela finalidade (exatamente 1 FK
preenchida, ``ck_link_acesso_alvo_por_finalidade``).

So-PG (migration 0049): ``ck_link_acesso_token_hash_formato`` e
``tg_link_acesso_imutavel`` (depois de criado, so ``usado_em``/``revogado_em``
podem ser preenchidos, uma vez cada; DELETE livre — purga/cascata).
"""
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now
from django.utils import timezone

from ..db.campos import HashSha256Field
from ..db.indices import Indice

_ALVOS = ('notificacao', 'resposta_formulario', 'cliente', 'profissional')


def _so_alvo(campo):
    """Q: `campo` preenchido e todos os outros alvos nulos."""
    return Q(**{f'{campo}__isnull': False}, **{f'{c}__isnull': True for c in _ALVOS if c != campo})


class LinkAcesso(models.Model):
    CONFIRMAR_PRESENCA = 'CONFIRMAR_PRESENCA'
    NPS = 'NPS'
    TERMO = 'TERMO'
    FORMULARIO = 'FORMULARIO'
    DESCADASTRO = 'DESCADASTRO'
    FEED_ICS = 'FEED_ICS'
    FINALIDADE_CHOICES = [
        (CONFIRMAR_PRESENCA, 'Confirmar presença'),
        (NPS, 'Pesquisa NPS'),
        (TERMO, 'Aceite de termo'),
        (FORMULARIO, 'Ficha/pesquisa'),
        (DESCADASTRO, 'Descadastro de comunicação'),
        (FEED_ICS, 'Feed do calendário (ICS)'),
    ]
    # finalidade -> atributo do alvo (o CHECK do banco exige exatamente este preenchido)
    CAMPO_ALVO = {
        CONFIRMAR_PRESENCA: 'notificacao',
        NPS: 'notificacao',
        TERMO: 'notificacao',
        FORMULARIO: 'resposta_formulario',
        DESCADASTRO: 'cliente',
        FEED_ICS: 'profissional',
    }

    finalidade = models.CharField(max_length=30, choices=FINALIDADE_CHOICES)
    # FKs sem indice automatico: cobertas pelos ix_link_acesso_*_parcial do Meta
    notificacao = models.ForeignKey(
        'Notificacao', on_delete=models.CASCADE, blank=True, null=True,
        db_index=False, related_name='links_acesso',
    )
    resposta_formulario = models.ForeignKey(
        'RespostaAnamnese', on_delete=models.CASCADE, blank=True, null=True,
        db_index=False, related_name='links_acesso',
    )
    cliente = models.ForeignKey(
        'Cliente', on_delete=models.CASCADE, blank=True, null=True,
        db_index=False, related_name='links_acesso',
    )
    profissional = models.ForeignKey(
        'Profissional', on_delete=models.CASCADE, blank=True, null=True,
        db_index=False, related_name='links_acesso',
    )
    token_hash = HashSha256Field(
        editable=False,
        db_comment=(
            'sha256(token) hex. Sem pepper/HMAC de proposito: rotacionar a SECRET_KEY '
            '(incidente, PROJECT §9.5) nao pode invalidar links ja enviados.'
        ),
    )
    expira_em = models.DateTimeField(blank=True, null=True)
    usado_em = models.DateTimeField(
        blank=True, null=True,
        db_comment=(
            'Primeiro uso efetivo (acao concluida). Links de uso multiplo (ICS, descadastro) '
            'continuam validos.'
        ),
    )
    revogado_em = models.DateTimeField(
        blank=True, null=True,
        db_comment='Revogado (rotacao do ICS, anonimizacao). Link revogado = 404.',
    )
    criado_em = models.DateTimeField(
        default=timezone.now, db_default=Now(), editable=False, db_column='created_at',
    )

    class Meta:
        db_table = 'link_acesso'
        db_table_comment = (
            'Link publico com token (link magico). So o SHA-256 do token e gravado: o token em '
            'claro existe apenas na URL entregue. Alvo tipado por finalidade (exatamente 1 FK '
            'preenchida).'
        )
        constraints = [
            models.UniqueConstraint(fields=['token_hash'], name='uq_link_acesso_token_hash'),
            models.CheckConstraint(
                condition=Q(finalidade__in=[
                    'CONFIRMAR_PRESENCA', 'NPS', 'TERMO', 'FORMULARIO', 'DESCADASTRO', 'FEED_ICS',
                ]),
                name='ck_link_acesso_finalidade',
            ),
            models.CheckConstraint(
                condition=(
                    (Q(finalidade__in=['CONFIRMAR_PRESENCA', 'NPS', 'TERMO']) & _so_alvo('notificacao'))
                    | (Q(finalidade='FORMULARIO') & _so_alvo('resposta_formulario'))
                    | (Q(finalidade='DESCADASTRO') & _so_alvo('cliente'))
                    | (Q(finalidade='FEED_ICS') & _so_alvo('profissional'))
                ),
                name='ck_link_acesso_alvo_por_finalidade',
            ),
        ]
        indexes = [
            Indice(fields=['notificacao'], condition=Q(notificacao__isnull=False),
                   name='ix_link_acesso_notificacao_parcial'),
            Indice(fields=['resposta_formulario'], condition=Q(resposta_formulario__isnull=False),
                   name='ix_link_acesso_resposta_formulario_parcial'),
            Indice(fields=['cliente'], condition=Q(cliente__isnull=False),
                   name='ix_link_acesso_cliente_parcial'),
            Indice(fields=['profissional'], condition=Q(profissional__isnull=False),
                   name='ix_link_acesso_profissional_parcial'),
            Indice(fields=['expira_em'], condition=Q(expira_em__isnull=False),
                   name='ix_link_acesso_expira_em_parcial'),
        ]

    def __str__(self):
        return f'{self.get_finalidade_display()} #{self.pk} ({self.alvo_id_rotulo})'

    @property
    def alvo_id_rotulo(self):
        campo = self.CAMPO_ALVO.get(self.finalidade)
        return f'{campo} {getattr(self, f"{campo}_id", None)}' if campo else '-'

    @property
    def alvo(self):
        """O objeto alvo da finalidade (notificacao, ficha, cliente ou profissional)."""
        campo = self.CAMPO_ALVO.get(self.finalidade)
        return getattr(self, campo) if campo else None

    @property
    def expirado(self):
        return self.expira_em is not None and self.expira_em <= timezone.now()

    @property
    def revogado(self):
        return self.revogado_em is not None
