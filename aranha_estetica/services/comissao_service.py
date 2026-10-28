"""Comissao Profissional — livro-razao (docs/specs/padrao-sql.md §4.f, migration 0053).

Lancamentos append-only em ``movimento_comissao``:
  CREDITO   — calcular_comissao (AtendimentoRealizado); 1 por atendimento, mesmo
              depois de estornado (UNIQUE parcial = idempotencia)
  PAGAMENTO — marcar_paga (baixa no painel, com autor); referencia o CREDITO
  ESTORNO   — estornar_comissao (AtendimentoCancelado); referencia o CREDITO,
              pago ou nao
Status (PENDENTE/PAGA/ESTORNADA) e saldo sao derivados. Saldo a pagar por
profissional = Σ CREDITO − Σ PAGAMENTO − Σ ESTORNO: fica NEGATIVO quando uma
comissao ja paga e estornada (desconta do proximo repasse). Sem baixa parcial
(PAGAMENTO = valor do credito).

Regra mais especifica vence:
  1. (profissional, procedimento)
  2. (profissional, qualquer procedimento)
  3. (qualquer profissional, procedimento)
  4. (qualquer, qualquer) — fallback global
"""
from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.db import IntegrityError, transaction
from django.db.models import Case, DecimalField, F, Q, Sum, When

from ..models import Atendimento, ConsumoSessao, MovimentoComissao, Profissional, RegraComissao

logger = logging.getLogger(__name__)

CENTAVO = Decimal('0.01')
CEM = Decimal('100')
ZERO = Decimal('0.00')

CREDITO = MovimentoComissao.TIPO_CREDITO
PAGAMENTO = MovimentoComissao.TIPO_PAGAMENTO
ESTORNO = MovimentoComissao.TIPO_ESTORNO


def _centavos(valor: Decimal) -> Decimal:
    """Arredonda em centavos meio-para-cima — mesmo criterio de utils/precos."""
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


# valor com o sinal do tipo (CREDITO +, PAGAMENTO/ESTORNO -) — espelho de vw_comissao_saldo
VALOR_COM_SINAL = Case(
    When(tipo=CREDITO, then=F('valor')),
    default=-F('valor'),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)


class ComissaoService:
    """Calcula, paga e estorna comissoes (lancamentos)."""

    @staticmethod
    def resolver_regra(profissional_id: int, procedimento_id: int) -> Optional[RegraComissao]:
        """Retorna regra mais especifica ativa, None se nada bater."""
        match_filter = (
            Q(profissional_id=profissional_id, procedimento_id=procedimento_id)
            | Q(profissional_id=profissional_id, procedimento_id__isnull=True)
            | Q(profissional_id__isnull=True, procedimento_id=procedimento_id)
            | Q(profissional_id__isnull=True, procedimento_id__isnull=True)
        )
        # order_by explicito: com 2 regras de mesma especificidade vence a
        # editada por ultimo (desempate por pk). Sem isso o sort estavel
        # herdava a ordem do banco, que nao e garantida.
        candidatos = list(
            RegraComissao.objects.filter(ativo=True).filter(match_filter)
            .order_by('-atualizado_em', '-pk')
        )

        # Score por especificidade: 3=ambos, 2=so prof, 1=so proc, 0=fallback
        def score(r: RegraComissao) -> int:
            return (1 if r.profissional_id else 0) * 2 + (1 if r.procedimento_id else 0)

        candidatos.sort(key=score, reverse=True)
        return candidatos[0] if candidatos else None

    @staticmethod
    def base_calculo(atendimento: Atendimento) -> Decimal:
        """Valor sobre o qual incide a comissao.

        Sessao debitada de pacote: valor unitario CONGELADO na venda
        (compra_pacote_item.valor_unitario = valor pago / sessoes da compra) — o
        valor_cobrado da sessao e o preco cheio do procedimento e inflaria a
        comissao. Avulso: valor_cobrado.
        """
        consumo = (
            ConsumoSessao.objects.select_related('compra_pacote_item')
            .filter(atendimento_id=atendimento.pk).first()
        )
        if consumo is None:
            return atendimento.valor_cobrado or ZERO
        return consumo.compra_pacote_item.valor_unitario

    @staticmethod
    @transaction.atomic
    def calcular_comissao(atendimento: Atendimento) -> Optional[MovimentoComissao]:
        """Lanca o CREDITO de comissao do atendimento REALIZADO. Idempotente."""
        if atendimento.status != Atendimento.STATUS_REALIZADO:
            return None
        if atendimento.eh_retorno:
            return None  # retorno gratis nao gera comissao
        base = ComissaoService.base_calculo(atendimento)
        if base <= 0:
            return None

        regra = ComissaoService.resolver_regra(
            atendimento.profissional_id, atendimento.procedimento_id,
        )
        if not regra:
            return None

        if regra.percentual is not None:
            percentual = regra.percentual
            if percentual > CEM:
                # Regra antiga/invalida (>100%) nunca paga mais que o atendimento
                # rendeu; o CHECK/validator do model barra novas.
                logger.warning('regra_comissao_percentual_acima_de_100', extra={'regra_id': regra.pk})
                percentual = CEM
            valor = _centavos(base * percentual / CEM)
        else:
            valor = regra.valor or ZERO

        if valor <= 0:
            return None

        # Idempotencia total por atendimento: 1 CREDITO, nunca um 2o (mesmo depois
        # de estornado) — previne double-pay quando um atendimento volta a Realizado.
        # O UNIQUE parcial (uq_movimento_comissao_credito_por_atendimento) garante no banco.
        if MovimentoComissao.objects.filter(atendimento=atendimento, tipo=CREDITO).exists():
            logger.info('comissao_ja_existe_ignorada', extra={'atendimento_id': atendimento.pk})
            return None

        try:
            with transaction.atomic():  # savepoint: a corrida perdida nao aborta o chamador
                movimento = MovimentoComissao.objects.create(
                    tipo=CREDITO,
                    profissional=atendimento.profissional,
                    atendimento=atendimento,
                    regra=regra,
                    valor=valor,
                )
        except IntegrityError:
            logger.info(
                'comissao_duplicada_ignorada',
                extra={'atendimento_id': atendimento.pk},
            )
            return None

        logger.info(
            'comissao_calculada',
            extra={
                'movimento_id': movimento.pk,
                'profissional_id': atendimento.profissional_id,
                'valor': str(valor),
            },
        )
        return movimento

    @staticmethod
    def _derivado(credito: MovimentoComissao, tipo: str, usuario=None, observacoes: str = ''):
        return MovimentoComissao.objects.create(
            tipo=tipo,
            movimento_origem=credito,
            profissional_id=credito.profissional_id,
            atendimento_id=credito.atendimento_id,
            valor=credito.valor,
            usuario_autor=usuario if getattr(usuario, 'pk', None) else None,
            observacoes=observacoes,
        )

    @staticmethod
    @transaction.atomic
    def marcar_paga(movimento_id: int, usuario=None) -> Optional[MovimentoComissao]:
        """Baixa de uma comissao PENDENTE: lanca o PAGAMENTO (valor integral, com autor).

        Trava o CREDITO (serializa pagar x estornar; no PG o trigger trava de novo).
        Retorna o PAGAMENTO, ou None se o credito nao existe ou ja foi pago/estornado.
        """
        credito = (
            MovimentoComissao.objects.select_for_update()
            .filter(pk=movimento_id, tipo=CREDITO)
            .first()
        )
        if credito is None:
            return None
        if MovimentoComissao.objects.filter(movimento_origem=credito, tipo__in=(PAGAMENTO, ESTORNO)).exists():
            return None
        try:
            with transaction.atomic():
                pagamento = ComissaoService._derivado(credito, PAGAMENTO, usuario)
        except IntegrityError:  # baixa concorrente ganhou (UNIQUE de pagamento por credito)
            return None
        logger.info(
            'comissao_paga',
            extra={'movimento_id': credito.pk, 'pagamento_id': pagamento.pk, 'valor': str(credito.valor)},
        )
        return pagamento

    @staticmethod
    @transaction.atomic
    def estornar_comissao(atendimento: Atendimento, usuario=None) -> int:
        """Lanca o ESTORNO do credito ainda nao estornado do atendimento (pago ou nao).

        Pago e depois estornado deixa o saldo do profissional negativo (a descontar
        do proximo repasse). Idempotente: retorna quantos estornos lancou (0 ou 1).
        O motivo livre do cancelamento NAO vai p/ o ledger (imutavel: texto livre
        pode ter dado pessoal que o esquecimento LGPD nao conseguiria apagar).
        """
        creditos = list(
            MovimentoComissao.objects.select_for_update()
            .filter(atendimento=atendimento, tipo=CREDITO)
        )
        lancados = 0
        for credito in creditos:
            if MovimentoComissao.objects.filter(movimento_origem=credito, tipo=ESTORNO).exists():
                continue
            try:
                with transaction.atomic():
                    ComissaoService._derivado(credito, ESTORNO, usuario, observacoes='atendimento cancelado')
            except IntegrityError:  # estorno concorrente ganhou
                continue
            lancados += 1
        if lancados:
            logger.info(
                'comissoes_estornadas',
                extra={'atendimento_id': atendimento.pk, 'count': lancados},
            )
        return lancados

    # ─── leitura ────────────────────────────────────────────────────────
    @staticmethod
    def saldos_por_profissional(profissional_id: Optional[int] = None) -> dict:
        """{profissional_id: saldo a pagar} de todo o ledger (pode ser negativo).

        Espelho de vw_comissao_saldo.valor_saldo_a_pagar / fn_saldo_comissao.
        """
        qs = MovimentoComissao.objects.all()
        if profissional_id is not None:
            qs = qs.filter(profissional_id=profissional_id)
        return {
            linha['profissional_id']: linha['saldo'] or ZERO
            for linha in qs.order_by().values('profissional_id').annotate(saldo=Sum(VALOR_COM_SINAL))
        }

    @staticmethod
    def saldo_a_pagar(profissional) -> Decimal:
        pk = getattr(profissional, 'pk', profissional)
        return ComissaoService.saldos_por_profissional(pk).get(pk, ZERO)

    @staticmethod
    def resumo_por_profissional(creditos, profissional_id: Optional[int] = None) -> list:
        """Linhas do painel por profissional: pendente/paga/qtd dos CREDITOs do filtro
        (``creditos`` = creditos_com_status() ja filtrado) + saldo a pagar do ledger
        inteiro. Profissional sem credito no periodo mas com saldo != 0 tambem aparece
        (saldo negativo nao some do painel)."""
        linhas = {}
        for prof_id, nome, status, valor in creditos.order_by().values_list(
            'profissional_id', 'profissional__nome', 'status', 'valor',
        ):
            linha = linhas.setdefault(prof_id, {
                'profissional_id': prof_id, 'profissional__nome': nome,
                'pendente': ZERO, 'paga': ZERO, 'qtd': 0,
            })
            if status == MovimentoComissao.STATUS_ESTORNADA:
                continue
            linha['qtd'] += 1
            if status == MovimentoComissao.STATUS_PENDENTE:
                linha['pendente'] += valor
            else:
                linha['paga'] += valor
        saldos = ComissaoService.saldos_por_profissional(profissional_id)
        faltam = [pk for pk, saldo in saldos.items() if saldo and pk not in linhas]
        for prof_id, nome in Profissional.objects.filter(pk__in=faltam).values_list('pk', 'nome'):
            linhas[prof_id] = {'profissional_id': prof_id, 'profissional__nome': nome,
                               'pendente': ZERO, 'paga': ZERO, 'qtd': 0}
        for linha in linhas.values():
            linha['saldo'] = saldos.get(linha['profissional_id'], ZERO)
            linha['saldo_negativo'] = linha['saldo'] < 0
        return sorted(linhas.values(), key=lambda r: (-r['pendente'], r['profissional__nome'] or ''))

