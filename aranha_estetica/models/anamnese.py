# aranha_estetica/models/anamnese.py — Formularios pre/pos atendimento
import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from .clientes import Cliente
from .procedimentos import Procedimento

_ANAMNESE_TIPOS_CAMPO = {
    'bool', 'text', 'longtext', 'select', 'scale',
    'checkboxes', 'number', 'date', 'email',
}


class FormularioAnamnese(models.Model):
    """Template de questionario (anamnese pre-atendimento OU pesquisa pos-atendimento).

    schema_json: lista de campos no formato:
      [
        {"key": "gestante", "tipo": "bool", "label": "Esta gestante?", "obrigatorio": true},
        {"key": "alergias", "tipo": "text", "label": "Possui alergias?", "obrigatorio": false},
        {"key": "cirurgias", "tipo": "longtext", "label": "Cirurgias previas", "obrigatorio": false},
        {"key": "idade_aprox", "tipo": "select", "label": "Idade", "opcoes": ["<18","18-30","31-50",">50"], "obrigatorio": true},
        {"key": "satisfacao", "tipo": "scale", "label": "Como avalia?", "opcoes": ["1","2","3","4","5"], "obrigatorio": true},
        {"key": "melhorias", "tipo": "checkboxes", "label": "O que faltou?", "opcoes": ["Foto","Video"], "obrigatorio": false}
      ]
    Tipos suportados: bool, text, longtext, select, scale, checkboxes, number, date, email.

    Tipo do formulario:
      ANAMNESE  — pre-atendimento, gatilho na criacao do agendamento
      PESQUISA  — pos-atendimento, gatilho ao marcar REALIZADO
    """

    ESCOPO_CHOICES = [
        ('GLOBAL', 'Global (todo agendamento)'),
        ('CATEGORIA', 'Por categoria de procedimento'),
        ('PROCEDIMENTO', 'Por procedimento especifico'),
        ('MODALIDADE', 'Por modalidade (presencial/online/hibrido)'),
    ]

    TIPO_CHOICES = [
        ('ANAMNESE', 'Anamnese pre-atendimento'),
        ('PESQUISA', 'Pesquisa pos-atendimento'),
    ]

    nome = models.CharField(max_length=120)
    tipo = models.CharField(
        max_length=12, choices=TIPO_CHOICES, default='ANAMNESE', db_index=True,
    )
    escopo = models.CharField(max_length=20, choices=ESCOPO_CHOICES, default='GLOBAL')
    categoria = models.CharField(max_length=20, blank=True, default='')
    modalidade = models.CharField(max_length=12, blank=True, default='')
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
            models.Index(fields=['ativo', 'tipo'], name='idx_anamnese_ativo_tipo'),
        ]

    def __str__(self):
        return f'{self.nome} ({self.get_tipo_display()} | {self.get_escopo_display()})'

    def clean(self):
        """Valida formato do schema_json (lista de campos com keys obrigatorias)."""
        super().clean()
        schema = self.schema_json
        if schema is None:
            return
        if not isinstance(schema, list):
            raise ValidationError({'schema_json': 'schema_json deve ser uma lista de campos.'})
        keys_vistas: set[str] = set()
        for idx, campo in enumerate(schema):
            if not isinstance(campo, dict):
                raise ValidationError({
                    'schema_json': f'campo[{idx}] deve ser objeto (dict).',
                })
            obrigatorios = {'key', 'tipo', 'label'}
            faltam = obrigatorios - campo.keys()
            if faltam:
                raise ValidationError({
                    'schema_json': f'campo[{idx}] faltando: {sorted(faltam)}',
                })
            key = campo['key']
            if not isinstance(key, str) or not key.strip():
                raise ValidationError({
                    'schema_json': f'campo[{idx}].key deve ser string nao-vazia.',
                })
            if key in keys_vistas:
                raise ValidationError({
                    'schema_json': f'key duplicada: {key!r}',
                })
            keys_vistas.add(key)
            tipo = campo['tipo']
            if tipo not in _ANAMNESE_TIPOS_CAMPO:
                raise ValidationError({
                    'schema_json':
                        f'campo[{idx}].tipo {tipo!r} invalido. '
                        f'Permitidos: {sorted(_ANAMNESE_TIPOS_CAMPO)}',
                })
            if tipo in ('select', 'scale', 'checkboxes'):
                opcoes = campo.get('opcoes')
                if not isinstance(opcoes, list) or not opcoes:
                    raise ValidationError({
                        'schema_json':
                            f'campo[{idx}] tipo {tipo!r} requer "opcoes" (lista nao-vazia).',
                    })


def _gen_resposta_token():
    return secrets.token_urlsafe(32)


class RespostaAnamnese(models.Model):
    """Resposta de cliente p/ um formulario, vinculada ao atendimento.

    token: gerado no momento do convite (workflow pre/pos). Link publico
    /pesquisa/<token>/ ou /anamnese/<token>/ permite preenchimento sem login.
    Apos POST com sucesso, respondida_em fica populado e link nao reabre form.
    """

    formulario = models.ForeignKey(FormularioAnamnese, on_delete=models.RESTRICT)
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    atendimento = models.ForeignKey('Atendimento', on_delete=models.CASCADE, blank=True, null=True)
    token = models.CharField(
        max_length=64, unique=True, db_index=True, default=_gen_resposta_token,
    )
    respostas_json = models.JSONField(default=dict)
    criado_em = models.DateTimeField(auto_now_add=True)
    respondida_em = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'resposta_anamnese'
        indexes = [
            models.Index(fields=['cliente', '-criado_em'], name='idx_anamnese_resp_cli'),
            models.Index(fields=['atendimento'], name='idx_anamnese_resp_atend'),
            models.Index(fields=['respondida_em'], name='idx_anamnese_resp_data'),
        ]

    def __str__(self):
        return f'Resposta {self.formulario.nome} - {self.cliente.nome}'

    def get_link_publico(self):
        """Retorna path publico baseado no tipo do formulario."""
        if self.formulario.tipo == 'PESQUISA':
            return reverse('aranha:pesquisa_publica', args=[self.token])
        return reverse('aranha:anamnese_publica', args=[self.token])

    @property
    def respondida(self):
        return self.respondida_em is not None
