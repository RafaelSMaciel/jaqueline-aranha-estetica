"""Numeros do site publico (prova social): so aparece o que o banco ou a dona sustentam.

Regras (o template esconde o numero que vier None):

- nota media: media das notas da pesquisa de satisfacao (AvaliacaoNPS, 0-10) em escala de 5
  estrelas (nota / 2), 1 casa decimal, arredondada "meio p/ cima". So com MIN_AVALIACOES respostas:
  com 3 notas 10 a media "5,0" diz mais sobre o acaso que sobre a clinica.
- clientes atendidas: clientes distintas com ao menos um atendimento REALIZADO, arredondado PARA
  BAIXO ("127" vira "120+", "1.234" vira "1.200+": o "+" nunca promete mais do que houve). So a
  partir de MIN_CLIENTES.
- tratamentos: procedimentos que o agendamento online oferece hoje (ativos com profissional ativo
  habilitado — services/disponibilidade.procedimentos_agendaveis), no total e por categoria.
- anos de experiencia: a regra operacional `anos_experiencia` (models/configuracao), que a dona
  preenche no painel; em branco = nao aparece.

Uma conta a cada CACHE_TTL (4 consultas agregadas, nenhuma por item) e nao a cada visita; quem muda
um desses numeros espera no maximo 10 minutos para ver no site.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import TypedDict

from django.core.cache import cache
from django.db.models import Avg, Count

from ..models import Atendimento, AvaliacaoNPS, Configuracao
from .disponibilidade import procedimentos_agendaveis

MIN_AVALIACOES = 10
MIN_CLIENTES = 50
CACHE_CHAVE = 'site:prova_social:v1'
CACHE_TTL = 10 * 60


class NumerosDoSite(TypedDict):
    nota_media: Decimal | None          # 0,0 a 5,0 (None: menos de MIN_AVALIACOES respostas)
    estrelas: list[str]                 # 5 itens 'cheia' | 'meia' | 'vazia' (vazio sem nota)
    total_avaliacoes: int
    clientes_atendidas: int | None      # ja arredondado p/ baixo (None: abaixo de MIN_CLIENTES)
    tratamentos: int                    # procedimentos agendaveis
    tratamentos_por_categoria: dict[str, int]
    anos_experiencia: int | None


def _nota_em_estrelas(media_0_a_10) -> Decimal:
    return (Decimal(str(media_0_a_10)) / 2).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def estrelas(nota: Decimal | None) -> list[str]:
    """Desenho das 5 estrelas sem arredondar para cima: 4,7 -> 4 cheias e meia; 4,8 -> 5 cheias."""
    if nota is None:
        return []
    desenho = []
    for i in range(1, 6):
        if nota >= Decimal(i) - Decimal('0.25'):
            desenho.append('cheia')
        elif nota >= Decimal(i) - Decimal('0.75'):
            desenho.append('meia')
        else:
            desenho.append('vazia')
    return desenho


def arredondar_para_baixo(n: int) -> int:
    """Degrau "honesto" do contador: dezenas ate 999, centenas dali em diante."""
    passo = 10 if n < 1000 else 100
    return n - n % passo


def _anos_experiencia() -> int | None:
    valor = Configuracao.ler('anos_experiencia')
    return valor if isinstance(valor, int) and valor > 0 else None


def calcular() -> NumerosDoSite:
    """Conta sem cache (o site usa numeros_do_site)."""
    nps = AvaliacaoNPS.objects.aggregate(media=Avg('nota'), total=Count('id'))
    total_avaliacoes = nps['total'] or 0
    nota = (_nota_em_estrelas(nps['media'])
            if total_avaliacoes >= MIN_AVALIACOES and nps['media'] is not None else None)

    clientes = Atendimento.objects.realizados().aggregate(n=Count('cliente', distinct=True))['n'] or 0

    por_categoria = {
        linha['categoria']: linha['n']
        for linha in procedimentos_agendaveis().order_by().values('categoria').annotate(n=Count('id', distinct=True))
    }
    return {
        'nota_media': nota,
        'estrelas': estrelas(nota),
        'total_avaliacoes': total_avaliacoes,
        'clientes_atendidas': arredondar_para_baixo(clientes) if clientes >= MIN_CLIENTES else None,
        'tratamentos': sum(por_categoria.values()),
        'tratamentos_por_categoria': por_categoria,
        'anos_experiencia': _anos_experiencia(),
    }


def numeros_do_site() -> NumerosDoSite:
    """Os numeros do site, do cache (CACHE_TTL) ou recalculados."""
    numeros = cache.get(CACHE_CHAVE)
    if numeros is None:
        numeros = calcular()
        cache.set(CACHE_CHAVE, numeros, CACHE_TTL)
    return numeros


def invalidar_cache() -> None:
    cache.delete(CACHE_CHAVE)
