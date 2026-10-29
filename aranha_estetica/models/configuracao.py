# aranha_estetica/models/configuracao.py — Configuracao e o registro das regras operacionais
"""Regras operacionais que a dona ajusta no painel, num registro tipado (CHAVES).

Antes a tabela era chave-valor livre: a tela listava tudo (marca, cursor interno de envio), aceitava
chave inventada e so validava email_admin; MAX_FALTAS_BLOQUEIO='abc' caia no padrao sem aviso. Agora
cada regra tem tipo, limites e padrao aqui; a tela Regras operacionais lista e grava so estas chaves
(forms/cadastros.ConfiguracaoForm) e o codigo le pelo registro:

    Configuracao.ler('email_admin')          # -> str ('' = sem destino proprio)
    Configuracao.ler('MAX_FALTAS_BLOQUEIO')  # -> int de 1 a 10

`ler` e nao `valor` porque `valor` ja e a coluna. A marca e os contatos (utils/branding.BRANDING_FIELDS)
usam a mesma tabela, com a tela Identidade visual e validacao propria.
"""
import logging
import re
from dataclasses import dataclass
from functools import reduce
from operator import or_

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models

from ..constants import MAX_FALTAS_ANTES_BLOQUEIO
from .mixins import atualizado_em_legado, criado_em_legado

logger = logging.getLogger(__name__)

TIPO_EMAIL = 'email'
_INTEIRO = re.compile(r'-?[0-9]{1,9}')


@dataclass(frozen=True)
class Chave:
    """Uma regra operacional: rotulo e explicacao p/ a tela, tipo (TIPO_EMAIL ou int), faixa e padrao.

    opcional: inteiro que pode ficar em branco ('' = a dona nao informou; quem le esconde o que
    dependeria dele, ex.: os anos de experiencia do site)."""
    rotulo: str
    descricao: str
    tipo: type | str
    default: str | int = ''
    minimo: int | None = None
    maximo: int | None = None
    opcional: bool = False

    def validar(self, bruto) -> str | int:
        """Texto da tela/banco -> valor tipado. ValidationError com a mensagem p/ a equipe."""
        texto = str(bruto if bruto is not None else '').strip()
        if self.opcional and not texto:
            return ''
        if self.tipo == TIPO_EMAIL:
            if texto:
                try:
                    validate_email(texto)
                except ValidationError as exc:
                    raise ValidationError('Informe um e-mail válido.', code='invalid') from exc
            return texto
        # fullmatch de digitos ASCII: int() aceitaria '٣' (digito arabe) e '1_0'
        if not _INTEIRO.fullmatch(texto):
            raise ValidationError(f'Use um número inteiro de {self.minimo} a {self.maximo}.', code='invalid')
        numero = int(texto)
        if (self.minimo is not None and numero < self.minimo) or (self.maximo is not None and numero > self.maximo):
            raise ValidationError(f'Use um número inteiro de {self.minimo} a {self.maximo}.', code='fora_da_faixa')
        return numero


# Somente o que o codigo de fato le (grep antes de acrescentar outra). Quem le: services/nps.destino_alerta
# (email_admin), Cliente.limite_faltas_bloqueio (MAX_FALTAS_BLOQUEIO, ainda direto; P14 passa p/ ler())
# e services/prova_social.numeros_do_site (anos_experiencia).
CHAVES: dict[str, Chave] = {
    'email_admin': Chave(
        rotulo='E-mail do alerta de avaliação negativa',
        descricao=('Recebe o aviso de cada nota de 0 a 6 na pesquisa de satisfação. Em branco, o aviso vai '
                   'para o e-mail de contato da clínica.'),
        tipo=TIPO_EMAIL,
        default='',
    ),
    'MAX_FALTAS_BLOQUEIO': Chave(
        rotulo='Faltas seguidas para pausar o agendamento online',
        descricao=('Ao chegar a este número de faltas seguidas, a cliente passa a marcar pela clínica '
                   '(WhatsApp ou painel). Um atendimento realizado zera a contagem.'),
        tipo=int,
        minimo=1,
        maximo=10,
        default=MAX_FALTAS_ANTES_BLOQUEIO,
    ),
    'anos_experiencia': Chave(
        rotulo='Anos de experiência (site)',
        descricao=('Aparece na página inicial e em "Sobre" como "X anos de experiência". Use "Restaurar '
                   'padrão" para deixar em branco: sem o número, o site não mostra essa informação.'),
        tipo=int,
        minimo=1,
        maximo=60,
        default='',
        opcional=True,
    ),
}


def chave_do_registro(chave) -> str | None:
    """Nome canonico da regra (o do registro) p/ uma chave em qualquer caixa; None = fora do registro."""
    alvo = str(chave or '').strip().lower()
    return next((nome for nome in CHAVES if nome.lower() == alvo), None)


class Configuracao(models.Model):
    chave = models.CharField(max_length=100)
    valor = models.TextField(blank=True, default='', db_default='')
    descricao = models.TextField(blank=True, default='', db_default='')
    criado_em = criado_em_legado()
    atualizado_em = atualizado_em_legado()

    class Meta:
        managed = True
        db_table = 'configuracao'
        db_table_comment = (
            'Chave-valor editavel no painel: Branding, email_admin, MAX_FALTAS_BLOQUEIO. '
            '(prontuario_perguntas saiu p/ formulario tipo PRONTUARIO.)'
        )
        constraints = [
            models.UniqueConstraint(fields=['chave'], name='uq_configuracao_chave'),
        ]

    def __str__(self):
        return f'{self.chave}: {self.valor}'

    @classmethod
    def das_regras(cls) -> dict:
        """{nome canonico: linha} das regras do registro ja gravadas (a de nome exato vence a de outra caixa)."""
        linhas = {}
        filtro = reduce(or_, (models.Q(chave__iexact=nome) for nome in CHAVES))
        for linha in cls.objects.filter(filtro).order_by('chave', 'pk'):
            nome = chave_do_registro(linha.chave)
            if nome not in linhas or linha.chave == nome:
                linhas[nome] = linha
        return linhas

    @classmethod
    def ler(cls, chave: str):
        """Valor tipado da regra (KeyError p/ chave fora do registro: erro de programacao).

        Sem linha, em branco ou com valor que o registro recusa (gravado antes da validacao, ou pelo
        Django admin) -> o padrao da regra, com aviso no log (antes caia no padrao em silencio)."""
        definicao = CHAVES[chave]
        linha = cls.das_regras().get(chave)
        bruto = linha.valor if linha is not None else ''
        if not str(bruto).strip():
            return definicao.default
        try:
            return definicao.validar(bruto)
        except ValidationError:
            logger.warning('configuracao_valor_invalido', extra={'chave': chave})
            return definicao.default
