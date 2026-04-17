# app_shivazen/models/nps.py — Avaliacao NPS
from django.db import models


class AvaliacaoNPS(models.Model):
    atendimento = models.OneToOneField('Atendimento', on_delete=models.CASCADE)
    nota = models.SmallIntegerField()
    comentario = models.TextField(blank=True, null=True)
    alerta_enviado = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'avaliacao_nps'
        constraints = [
            models.CheckConstraint(
                check=models.Q(nota__gte=0) & models.Q(nota__lte=10),
                name='chk_avaliacao_nps_nota'
            )
        ]

    def __str__(self):
        cliente_nome = (
            self.atendimento.cliente.nome_completo
            if self.atendimento_id and self.atendimento.cliente_id
            else 's/ cliente'
        )
        return f'NPS {self.nota}/10 — {cliente_nome}'
