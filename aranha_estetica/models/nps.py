# aranha_estetica/models/nps.py — Avaliacao NPS
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Now

from ..db.indices import Indice
from ..excecoes import RegraDeNegocio
from .mixins import atualizado_em_legado


class AvaliacaoNPS(models.Model):
    atendimento = models.OneToOneField('Atendimento', on_delete=models.CASCADE)
    nota = models.SmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(10)],
    )
    comentario = models.TextField(blank=True, default='', db_default='')
    # Depoimento publico (LGPD): exige opt-in do cliente E aprovacao da equipe.
    autoriza_publicacao = models.BooleanField(default=False, db_default=False, db_column='eh_publicacao_autorizada')
    aprovado_publicacao = models.BooleanField(default=False, db_default=False, db_column='eh_publicacao_aprovada')
    alerta_enviado = models.BooleanField(
        default=False, db_default=False, db_column='eh_alerta_enviado',
        db_comment='Detrator (nota <= 6) ja avisado por e-mail a clinica.',
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now(), db_column='created_at')
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'avaliacao_nps'
        db_table_comment = (
            'Nota 0-10 do atendimento. Depoimento publico exige opt-in do cliente (eh_publicacao_autorizada) '
            'E moderacao (eh_publicacao_aprovada).'
        )
        constraints = [
            models.CheckConstraint(
                condition=models.Q(nota__gte=0) & models.Q(nota__lte=10),
                name='ck_avaliacao_nps_nota_0_10'
            ),
            models.CheckConstraint(
                condition=models.Q(aprovado_publicacao=False) | models.Q(autoriza_publicacao=True),
                name='ck_avaliacao_nps_aprovacao_exige_autorizacao',
            ),
        ]
        indexes = [
            Indice(fields=['criado_em'], name='ix_avaliacao_nps_created_at'),
            # services/nps.alertar_detratores: quase sempre vazio
            Indice(
                fields=['criado_em'], condition=models.Q(nota__lte=6, alerta_enviado=False),
                name='ix_avaliacao_nps_alerta_pendente_parcial',
            ),
            # pagina publica de depoimentos
            Indice(
                fields=['-criado_em'],
                condition=models.Q(nota__gte=9, autoriza_publicacao=True, aprovado_publicacao=True),
                name='ix_avaliacao_nps_depoimento_parcial',
            ),
        ]

    def clean(self):
        # espelha ck_avaliacao_nps_aprovacao_exige_autorizacao (0055): sem o opt-in da cliente a
        # equipe nao aprova (o Django admin mostra o erro em vez de 500)
        super().clean()
        if self.aprovado_publicacao and not self.autoriza_publicacao:
            raise ValidationError(
                {'aprovado_publicacao': 'A cliente não autorizou publicar este depoimento: não dá para aprovar.'}
            )

    def aprovar_publicacao(self) -> None:
        """Depoimento vai p/ o site (/depoimentos/): so com o opt-in da cliente (LGPD) e comentario."""
        if not self.autoriza_publicacao:
            raise RegraDeNegocio('A cliente não autorizou publicar este depoimento.', codigo='sem_autorizacao')
        if not (self.comentario or '').strip():
            raise RegraDeNegocio('Avaliação sem comentário não vira depoimento.', codigo='sem_comentario')
        self.aprovado_publicacao = True
        self.save(update_fields=['aprovado_publicacao', 'atualizado_em'])

    def retirar_publicacao(self) -> None:
        """Tira o depoimento do site (a cliente pode pedir a qualquer momento)."""
        self.aprovado_publicacao = False
        self.save(update_fields=['aprovado_publicacao', 'atualizado_em'])

    def __str__(self):
        cliente_nome = (
            self.atendimento.cliente.nome
            if self.atendimento_id and self.atendimento.cliente_id
            else 's/ cliente'
        )
        return f'NPS {self.nota}/10 — {cliente_nome}'
