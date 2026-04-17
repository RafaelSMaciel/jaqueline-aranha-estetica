# app_shivazen/models/prontuario.py — Prontuario e anotacoes clinicas
from django.db import models

from .clientes import Cliente


class Prontuario(models.Model):
    """Anamnese base permanente do cliente."""
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE)
    alergias = models.TextField(blank=True, null=True)
    contraindicacoes = models.TextField(blank=True, null=True)
    historico_saude = models.TextField(blank=True, null=True)
    medicamentos_uso = models.TextField(blank=True, null=True)
    observacoes_gerais = models.TextField(blank=True, null=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'prontuario'


class ProntuarioPergunta(models.Model):
    TIPO_CHOICES = [
        ('TEXTO', 'Texto livre'),
        ('BOOLEAN', 'Sim / Não'),
        ('SELECAO', 'Seleção'),
    ]

    texto = models.TextField()
    tipo_resposta = models.CharField(max_length=50, choices=TIPO_CHOICES)
    ativa = models.BooleanField(default=True)

    class Meta:
        managed = True
        db_table = 'prontuario_pergunta'
        constraints = [
            models.CheckConstraint(
                check=models.Q(tipo_resposta__in=['TEXTO', 'BOOLEAN', 'SELECAO']),
                name='chk_prontuario_pergunta_tipo'
            )
        ]


class ProntuarioResposta(models.Model):
    """Respostas vinculadas ao prontuario (dados permanentes do cliente)."""
    prontuario = models.ForeignKey(Prontuario, on_delete=models.CASCADE)
    pergunta = models.ForeignKey(ProntuarioPergunta, on_delete=models.RESTRICT)
    resposta_texto = models.TextField(blank=True, null=True)
    resposta_boolean = models.BooleanField(blank=True, null=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'prontuario_resposta'
        unique_together = (('prontuario', 'pergunta'),)


class AnotacaoSessao(models.Model):
    """Observacoes clinicas especificas de cada atendimento."""
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.CASCADE, related_name='anotacoes'
    )
    usuario = models.ForeignKey('Usuario', on_delete=models.SET_NULL, null=True, blank=True)
    texto = models.TextField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'anotacao_sessao'
