# aranha_estetica/models/prontuario.py — Prontuario e anotacoes clinicas
from django.core.exceptions import ValidationError
from django.db import models

from .clientes import Cliente


class Prontuario(models.Model):
    """Anamnese base permanente do cliente.

    respostas_extras (JSONB): respostas do questionario configuravel —
    substitui o EAV ProntuarioPergunta/ProntuarioResposta (remodelagem v2.1
    fase 5). Schema das perguntas vive em Configuracao
    chave='prontuario_perguntas' (lista de {chave, texto, tipo}).
    Busca Postgres: Prontuario.objects.filter(respostas_extras__diabetes=True).
    PG: CHECK chk_prontuario_extras_objeto (jsonb_typeof = 'object', 0043).
    PROTECT: dado clinico com retencao de 20 anos — nunca cascateia.
    """
    cliente = models.OneToOneField(Cliente, on_delete=models.PROTECT)
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

    def __str__(self):
        return f'Prontuario {self.cliente_id}'

    def clean(self):
        super().clean()
        if self.respostas_extras is not None and not isinstance(self.respostas_extras, dict):
            raise ValidationError({'respostas_extras': 'Deve ser um objeto JSON ({"chave": valor}).'})


# ProntuarioPergunta/ProntuarioResposta removidos na remodelagem v2.1
# fase 5 — EAV substituido por Prontuario.respostas_extras (JSONB).


class AnotacaoSessao(models.Model):
    """Observacoes clinicas especificas de cada atendimento.

    Registro clinico com autor: append-only (correcao = nova anotacao).
    autor PROTECT — excluir o usuario apagaria a autoria (desative-o);
    autor_nome guarda o nome no momento da escrita (renomear o usuario
    nao reescreve o historico).
    """
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.PROTECT, related_name='anotacoes'
    )
    autor = models.ForeignKey('Usuario', on_delete=models.PROTECT, null=True, blank=True)
    autor_nome = models.CharField(max_length=100, blank=True, default='')
    texto = models.TextField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'anotacao_sessao'
        indexes = [
            models.Index(fields=['atendimento', '-criado_em'], name='idx_anot_atn_criado'),
        ]

    def save(self, *args, **kwargs):
        if self.autor_id and not self.autor_nome:
            self.autor_nome = (self.autor.nome or '')[:100]
        super().save(*args, **kwargs)
