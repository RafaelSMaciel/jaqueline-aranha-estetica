# aranha_estetica/models/anamnese.py — Formulario anamnese pre-agendamento
from django.db import models

from .clientes import Cliente
from .procedimentos import Procedimento


class FormularioAnamnese(models.Model):
    """Template de questionario por categoria/procedimento.

    schema_json: lista de campos no formato:
      [
        {"key": "gestante", "tipo": "bool", "label": "Esta gestante?", "obrigatorio": true},
        {"key": "alergias", "tipo": "text", "label": "Possui alergias?", "obrigatorio": false},
        {"key": "cirurgias", "tipo": "longtext", "label": "Cirurgias previas", "obrigatorio": false},
        {"key": "idade_aprox", "tipo": "select", "label": "Idade", "opcoes": ["<18","18-30","31-50",">50"], "obrigatorio": true}
      ]
    Tipos suportados: bool, text, longtext, select, number, date.
    """

    ESCOPO_CHOICES = [
        ('GLOBAL', 'Global (todo agendamento)'),
        ('CATEGORIA', 'Por categoria de procedimento'),
        ('PROCEDIMENTO', 'Por procedimento especifico'),
    ]

    nome = models.CharField(max_length=120)
    escopo = models.CharField(max_length=20, choices=ESCOPO_CHOICES, default='GLOBAL')
    categoria = models.CharField(max_length=20, blank=True, default='')
    procedimento = models.ForeignKey(
        Procedimento, on_delete=models.CASCADE, blank=True, null=True
    )
    schema_json = models.JSONField(default=list)
    ativo = models.BooleanField(default=True)
    obrigatorio = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        managed = True
        db_table = 'formulario_anamnese'
        indexes = [
            models.Index(fields=['ativo', 'escopo'], name='idx_anamnese_ativo_escopo'),
        ]

    def __str__(self):
        return f'{self.nome} ({self.get_escopo_display()})'


class RespostaAnamnese(models.Model):
    """Resposta de cliente p/ um formulario, vinculada ao atendimento."""

    formulario = models.ForeignKey(FormularioAnamnese, on_delete=models.RESTRICT)
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    atendimento = models.ForeignKey('Atendimento', on_delete=models.CASCADE, blank=True, null=True)
    respostas_json = models.JSONField(default=dict)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'resposta_anamnese'
        indexes = [
            models.Index(fields=['cliente', '-criado_em'], name='idx_anamnese_resp_cli'),
            models.Index(fields=['atendimento'], name='idx_anamnese_resp_atend'),
        ]

    def __str__(self):
        return f'Resposta {self.formulario.nome} - {self.cliente.nome_completo}'
