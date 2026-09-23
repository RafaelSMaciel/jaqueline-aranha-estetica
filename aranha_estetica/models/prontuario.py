# aranha_estetica/models/prontuario.py — Prontuario e anotacoes clinicas
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

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
    excluida (no PG, trigger trg_prontuario_versao_imutavel — 0046); autor
    SET_NULL + autor_nome: excluir o usuario nao apaga a autoria.
    """
    prontuario = models.ForeignKey(Prontuario, on_delete=models.PROTECT, related_name='versoes')
    autor = models.ForeignKey('Usuario', on_delete=models.SET_NULL, null=True, blank=True)
    autor_nome = models.CharField(max_length=100, blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)
    dados = models.JSONField(default=dict)

    objects = ProntuarioVersaoQuerySet.as_manager()

    class Meta:
        managed = True
        db_table = 'prontuario_versao'
        indexes = [
            models.Index(fields=['prontuario', '-criado_em'], name='idx_pront_versao'),
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
