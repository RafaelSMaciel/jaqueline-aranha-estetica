# aranha_estetica/models/prontuario.py — Prontuario e anotacoes clinicas
from django.db import models

from .clientes import Cliente


class Prontuario(models.Model):
    """Anamnese base permanente do cliente.

    respostas_extras (JSONB): respostas do questionario configuravel —
    substitui o EAV ProntuarioPergunta/ProntuarioResposta (remodelagem v2.1
    fase 5). Schema das perguntas vive em Configuracao
    chave='prontuario_perguntas' (lista de {chave, texto, tipo}).
    Busca Postgres: Prontuario.objects.filter(respostas_extras__diabetes=True).
    """
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE)
    alergias = models.TextField(blank=True, null=True)
    contraindicacoes = models.TextField(blank=True, null=True)
    historico_saude = models.TextField(blank=True, null=True)
    medicamentos_uso = models.TextField(blank=True, null=True)
    observacoes_gerais = models.TextField(blank=True, null=True)
    respostas_extras = models.JSONField(default=dict, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'prontuario'


# ProntuarioPergunta/ProntuarioResposta removidos na remodelagem v2.1
# fase 5 — EAV substituido por Prontuario.respostas_extras (JSONB).


class AnotacaoSessao(models.Model):
    """Observacoes clinicas especificas de cada atendimento."""
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.CASCADE, related_name='anotacoes'
    )
    autor = models.ForeignKey('Usuario', on_delete=models.SET_NULL, null=True, blank=True)
    texto = models.TextField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'anotacao_sessao'
        indexes = [
            models.Index(fields=['atendimento', '-criado_em'], name='idx_anot_atn_criado'),
        ]
