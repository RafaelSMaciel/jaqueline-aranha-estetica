# aranha_estetica/models/prontuario.py — Prontuario e anotacoes clinicas
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Now
from django.utils import timezone

from ..db.indices import Indice
from .clientes import Cliente
from .mixins import criado_em_legado


class Prontuario(models.Model):
    """Anamnese base permanente do cliente.

    respostas_extras (JSONB): respostas do questionario do prontuario
    {key: valor} — substitui o EAV ProntuarioPergunta/ProntuarioResposta
    (remodelagem v2.1 fase 5). As perguntas sao o FormularioAnamnese tipo
    PRONTUARIO ativo (0051; antes Configuracao 'prontuario_perguntas'),
    validadas por services/anamnese.validar_respostas.
    Busca Postgres: Prontuario.objects.filter(respostas_extras__diabetes=True).
    PG: CHECK ck_prontuario_respostas_objeto (jsonb_typeof = 'object'; 0043, nome desde a 0055).
    PROTECT: dado clinico com retencao de 20 anos — nunca cascateia.
    Edicao: ProntuarioVersao.registrar() guarda o estado anterior antes do save.
    """
    # Campos clinicos de texto livre (a foto de ProntuarioVersao leva estes +
    # respostas_extras)
    CAMPOS_CLINICOS = (
        'alergias', 'contraindicacoes', 'historico_saude',
        'medicamentos_uso', 'observacoes_gerais',
    )
    # Prontuario "de verdade": algum campo preenchido. prontuario_salvar grava ''
    # (nao None) e um GET antigo criava registro vazio — Exists(Prontuario)
    # mentia (lista do painel, purga LGPD). Uso: filter(Prontuario.Q_COM_CONTEUDO).
    Q_COM_CONTEUDO = (
        models.Q(alergias__gt='') | models.Q(contraindicacoes__gt='')
        | models.Q(historico_saude__gt='') | models.Q(medicamentos_uso__gt='')
        | models.Q(observacoes_gerais__gt='') | ~models.Q(respostas_extras={})
    )

    cliente = models.OneToOneField(Cliente, on_delete=models.PROTECT)
    alergias = models.TextField(blank=True, default='', db_default='')
    contraindicacoes = models.TextField(blank=True, default='', db_default='')
    historico_saude = models.TextField(blank=True, default='', db_default='')
    medicamentos_uso = models.TextField(blank=True, default='', db_default='')
    observacoes_gerais = models.TextField(blank=True, default='', db_default='')
    respostas_extras = models.JSONField(
        default=dict, db_default={}, blank=True, db_column='respostas',
        db_comment=(
            'Respostas do questionario do prontuario {key: valor}; keys = formulario.perguntas do formulario '
            'tipo PRONTUARIO ativo.'
        ),
    )
    criado_em = criado_em_legado()
    atualizado_em = models.DateTimeField(auto_now=True, db_default=Now())

    class Meta:
        managed = True
        db_table = 'prontuario'
        db_table_comment = (
            'Ficha clinica base 1:1 com o cliente (retencao 20 anos). Cada edicao grava antes a foto anterior '
            'em prontuario_versao; historico_alteracao registra qualquer escrita (inclusive fora da app).'
        )
        # PG (0043 -> 0055): ck_prontuario_respostas_objeto (jsonb_typeof = 'object', VALIDATE)

    def __str__(self):
        return f'Prontuario {self.cliente_id}'

    def clean(self):
        super().clean()
        if self.respostas_extras is not None and not isinstance(self.respostas_extras, dict):
            raise ValidationError({'respostas_extras': 'Deve ser um objeto JSON ({"chave": valor}).'})


# ProntuarioPergunta/ProntuarioResposta removidos na remodelagem v2.1
# fase 5 — EAV substituido por Prontuario.respostas_extras (JSONB).


class ProntuarioVersaoQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError('O histórico do prontuário é permanente: versões não podem ser excluídas.')

    delete.alters_data = True
    delete.queryset_only = True


class ProntuarioVersao(models.Model):
    """Historico append-only do prontuario (CFM 1.638/2002; LGPD art. 6 X).

    Cada edicao grava ANTES do save a foto do estado anterior (dados = campos
    clinicos + respostas_extras) com o autor da edicao: trocar/apagar alergias
    nao perde o que estava registrado quando um procedimento foi feito.
    Gravar por ProntuarioVersao.registrar(). Linha nunca e alterada nem
    excluida (no PG, trigger tg_prontuario_versao_imutavel — 0046/0054); autor
    SET_NULL + autor_nome: excluir o usuario nao apaga a autoria.
    """
    # FKs sem indice automatico: prontuario coberto pelo ix_; autor = autoria (spec §2.11)
    prontuario = models.ForeignKey(Prontuario, on_delete=models.PROTECT, related_name='versoes', db_index=False)
    autor = models.ForeignKey(
        'Usuario', on_delete=models.SET_NULL, null=True, blank=True, db_column='usuario_autor_id',
        db_index=False,
    )
    autor_nome = models.CharField(
        max_length=100, blank=True, default='', db_default='', db_column='usuario_autor_nome',
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())
    dados = models.JSONField(
        default=dict, db_default={},
        db_comment=(
            'Chaves = atributos Python (CAMPOS_CLINICOS + respostas_extras). E registro historico: nunca '
            'renomear as chaves.'
        ),
    )

    objects = ProntuarioVersaoQuerySet.as_manager()

    class Meta:
        managed = True
        db_table = 'prontuario_versao'
        db_table_comment = (
            'Historico append-only do prontuario (CFM 1.638/2002): foto do estado ANTERIOR a cada edicao. '
            'Imutavel (tg_prontuario_versao_imutavel; so anular usuario_autor_id e aceito).'
        )
        indexes = [
            Indice(fields=['prontuario', '-criado_em'], name='ix_prontuario_versao_prontuario_criado_em'),
        ]

    def __str__(self):
        quando = f' ({timezone.localtime(self.criado_em):%d/%m/%Y %H:%M})' if self.criado_em else ''
        return f'Versao do prontuario {self.prontuario_id}{quando}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Versão do prontuário é imutável: registre uma nova versão.')
        if self.autor_id and not self.autor_nome:
            self.autor_nome = (self.autor.nome or '')[:100]
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('O histórico do prontuário é permanente: versões não podem ser excluídas.')

    @staticmethod
    def _foto(valores):
        dados = {c: valores.get(c) or '' for c in Prontuario.CAMPOS_CLINICOS}
        extras = valores.get('respostas_extras')
        dados['respostas_extras'] = dict(extras) if isinstance(extras, dict) else {}
        return dados

    @classmethod
    def registrar(cls, prontuario, usuario):
        """Grava a foto do estado PERSISTIDO do prontuario (o anterior a edicao).

        Chamar dentro da transacao da edicao, ANTES de prontuario.save() — le do
        banco, entao pode ser chamado antes ou depois dos setattr. Estado
        anterior vazio sem versoes (1o preenchimento / registro criado agora)
        nao gera versao. Devolve a ProntuarioVersao criada ou None.
        """
        if prontuario is None or prontuario.pk is None:
            return None
        valores = (
            Prontuario.objects.filter(pk=prontuario.pk)
            .values(*Prontuario.CAMPOS_CLINICOS, 'respostas_extras')
            .first()
        )
        if valores is None:
            return None
        dados = cls._foto(valores)
        vazio = not any(dados[c] for c in Prontuario.CAMPOS_CLINICOS) and not dados['respostas_extras']
        if vazio and not cls.objects.filter(prontuario_id=prontuario.pk).exists():
            return None
        autor = usuario if getattr(usuario, 'is_authenticated', False) and getattr(usuario, 'pk', None) else None
        return cls.objects.create(prontuario_id=prontuario.pk, autor=autor, dados=dados)


MSG_ANOTACAO_IMUTAVEL = 'Anotação de sessão é registro clínico append-only: corrija com uma nova anotação.'


class AnotacaoSessaoQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError(MSG_ANOTACAO_IMUTAVEL)

    update.alters_data = True
    update.queryset_only = True

    def delete(self):
        raise ValidationError(MSG_ANOTACAO_IMUTAVEL)

    delete.alters_data = True
    delete.queryset_only = True


class AnotacaoSessao(models.Model):
    """Observacoes clinicas especificas de cada atendimento.

    Registro clinico com autor: append-only (correcao = nova anotacao).
    autor PROTECT — excluir o usuario apagaria a autoria (desative-o);
    autor_nome guarda o nome no momento da escrita (renomear o usuario
    nao reescreve o historico). No PG ``tg_anotacao_sessao_imutavel`` (0056)
    barra UPDATE/DELETE; no Python, save() de linha existente/delete()/QuerySet
    recusam (FKs PROTECT: o Django nunca precisa de UPDATE aqui).
    """
    # FKs sem indice automatico: atendimento coberto pelo ix_; autor = autoria (spec §2.11)
    atendimento = models.ForeignKey(
        'Atendimento', on_delete=models.PROTECT, related_name='anotacoes', db_index=False,
    )
    autor = models.ForeignKey(
        'Usuario', on_delete=models.PROTECT, null=True, blank=True, db_column='usuario_autor_id',
        db_index=False,
    )
    autor_nome = models.CharField(
        max_length=100, blank=True, default='', db_default='', db_column='usuario_autor_nome',
    )
    texto = models.TextField()
    criado_em = models.DateTimeField(auto_now_add=True, db_default=Now())

    objects = AnotacaoSessaoQuerySet.as_manager()

    class Meta:
        managed = True
        db_table = 'anotacao_sessao'
        db_table_comment = (
            'Nota clinica de um atendimento, append-only (correcao = nova anotacao; tg_anotacao_sessao_imutavel).'
        )
        indexes = [
            Indice(fields=['atendimento', '-criado_em'], name='ix_anotacao_sessao_atendimento_criado_em'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(MSG_ANOTACAO_IMUTAVEL)
        if self.autor_id and not self.autor_nome:
            self.autor_nome = (self.autor.nome or '')[:100]
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(MSG_ANOTACAO_IMUTAVEL)
