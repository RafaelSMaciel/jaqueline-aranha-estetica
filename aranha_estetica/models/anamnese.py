# aranha_estetica/models/anamnese.py — Formularios pre/pos atendimento
import secrets

from django.core.exceptions import ValidationError
from django.db import models

from .clientes import Cliente
from .procedimentos import Procedimento

_ANAMNESE_TIPOS_CAMPO = {
    'bool', 'text', 'longtext', 'select', 'scale',
    'checkboxes', 'number', 'date', 'email',
}


class FormularioAnamneseQuerySet(models.QuerySet):
    def prontuario_ativo(self):
        """O formulario PRONTUARIO ativo (no maximo 1 — uq_formulario_prontuario_ativo) ou None."""
        return self.filter(tipo=FormularioAnamnese.TIPO_PRONTUARIO, ativo=True).order_by('pk').first()


class FormularioAnamnese(models.Model):
    """Questionario unico (spec padrao-sql §4.d): ANAMNESE, PESQUISA e PRONTUARIO.

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
    Atributo opcional por pergunta (qualquer tipo de formulario):
      "alerta_saude": true  -> resposta positiva sempre vira alerta de saude
      "alerta_saude": false -> nunca vira alerta
      ausente               -> heuristica de utils/saude (_RISCO_RX sobre key + label)

    Tipo do formulario:
      ANAMNESE   — pre-atendimento, respondida pela cliente (RespostaAnamnese)
      PESQUISA   — pos-atendimento, respondida pela cliente (RespostaAnamnese)
      PRONTUARIO — perguntas extras da ficha, preenchidas pela equipe; respostas
                   em Prontuario.respostas_extras ({key: valor}). No maximo 1
                   ativo, sempre GLOBAL e nunca obrigatorio (CHECK/UNIQUE no
                   banco, migration 0051). A key e estavel: mudar o label nao
                   mexe nas respostas.

    Banco (0051, nomes finais; a tabela vira `formulario` na 0054):
    ck_formulario_tipo, ck_formulario_escopo, ck_formulario_escopo_coerente,
    ck_formulario_prontuario_global e uq_formulario_prontuario_ativo.
    """

    ESCOPO_CHOICES = [
        ('GLOBAL', 'Global (todo agendamento)'),
        ('CATEGORIA', 'Por categoria de procedimento'),
        ('PROCEDIMENTO', 'Por procedimento especifico'),
        ('MODALIDADE', 'Por modalidade (presencial/online/hibrido)'),
    ]

    TIPO_ANAMNESE = 'ANAMNESE'
    TIPO_PESQUISA = 'PESQUISA'
    TIPO_PRONTUARIO = 'PRONTUARIO'
    TIPO_CHOICES = [
        (TIPO_ANAMNESE, 'Anamnese pre-atendimento'),
        (TIPO_PESQUISA, 'Pesquisa pos-atendimento'),
        (TIPO_PRONTUARIO, 'Questionario do prontuario (equipe)'),
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

    objects = FormularioAnamneseQuerySet.as_manager()

    class Meta:
        managed = True
        db_table = 'formulario_anamnese'
        indexes = [
            models.Index(fields=['ativo', 'escopo'], name='idx_anamnese_ativo_escopo'),
            models.Index(fields=['ativo', 'tipo'], name='idx_anamnese_ativo_tipo'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(tipo__in=['ANAMNESE', 'PESQUISA', 'PRONTUARIO']),
                name='ck_formulario_tipo',
            ),
            models.CheckConstraint(
                condition=models.Q(escopo__in=['GLOBAL', 'CATEGORIA', 'PROCEDIMENTO', 'MODALIDADE']),
                name='ck_formulario_escopo',
            ),
            models.CheckConstraint(
                condition=(
                    (~models.Q(escopo='PROCEDIMENTO') | models.Q(procedimento__isnull=False))
                    & (~models.Q(escopo='CATEGORIA') | ~models.Q(categoria=''))
                    & (~models.Q(escopo='MODALIDADE') | ~models.Q(modalidade=''))
                ),
                name='ck_formulario_escopo_coerente',
                violation_error_message='Escolha a categoria, a modalidade ou o procedimento do escopo.',
            ),
            models.CheckConstraint(
                condition=~models.Q(tipo='PRONTUARIO') | models.Q(
                    escopo='GLOBAL', obrigatorio=False, procedimento__isnull=True,
                ),
                name='ck_formulario_prontuario_global',
                violation_error_message=(
                    'O questionário do prontuário vale para todas as clientes: escopo Global e não obrigatório.'
                ),
            ),
            models.UniqueConstraint(
                fields=['tipo'], condition=models.Q(tipo='PRONTUARIO', ativo=True),
                name='uq_formulario_prontuario_ativo',
                violation_error_message='Já existe um questionário do prontuário ativo: edite-o ou desative-o antes.',
            ),
        ]

    def __str__(self):
        return f'{self.nome} ({self.get_tipo_display()} | {self.get_escopo_display()})'

    @property
    def eh_prontuario(self) -> bool:
        return self.tipo == self.TIPO_PRONTUARIO

    def clean(self):
        """Valida o schema_json (lista de campos com keys obrigatorias). Os
        CHECK/UNIQUE do Meta sao validados pelo full_clean (validate_constraints)
        com a mensagem legivel de cada um."""
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
            if 'alerta_saude' in campo and not isinstance(campo['alerta_saude'], bool):
                raise ValidationError({
                    'schema_json': f'campo[{idx}].alerta_saude deve ser true ou false.',
                })


def _gen_resposta_token():
    """Sem uso no codigo: mantida porque a migration 0023 referencia (default antigo
    de resposta_anamnese.token, coluna removida na 0049)."""
    return secrets.token_urlsafe(32)


class RespostaAnamnese(models.Model):
    """Resposta de cliente p/ um formulario, vinculada ao atendimento.

    O link publico (/anamnese/<token>/ ou /pesquisa/<token>/, preenchimento sem
    login) e um LinkAcesso FORMULARIO emitido no convite
    (services.links.emitir_link_formulario). Apos o POST com sucesso,
    respondida_em fica populado e o link nao reabre o form.
    """

    formulario = models.ForeignKey(FormularioAnamnese, on_delete=models.RESTRICT)
    # PROTECT: resposta de anamnese e dado de saude (retencao legal)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)
    atendimento = models.ForeignKey('Atendimento', on_delete=models.PROTECT, blank=True, null=True)
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
        # Usa *_id (sem queries lazy) p/ evitar N+1 em listagens de admin nao prefetched.
        return f'Resposta form#{self.formulario_id} - cliente#{self.cliente_id}'

    def clean(self):
        # espelha o CHECK chk_resposta_anamnese_objeto (PG, migration 0043)
        super().clean()
        if self.respostas_json is not None and not isinstance(self.respostas_json, dict):
            raise ValidationError({'respostas_json': 'Deve ser um objeto JSON ({"chave": valor}).'})
        # PRONTUARIO e respondido pela equipe na ficha (Prontuario.respostas_extras)
        if self.formulario_id and self.formulario.tipo == FormularioAnamnese.TIPO_PRONTUARIO:
            raise ValidationError({'formulario': 'O questionário do prontuário não recebe respostas avulsas.'})

    @property
    def respondida(self):
        return self.respondida_em is not None
