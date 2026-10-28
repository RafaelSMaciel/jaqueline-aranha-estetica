"""Comissao Profissional — livro-razao (docs/specs/padrao-sql.md §4.f, migration 0053).

Lancamentos append-only em ``movimento_comissao``:
  CREDITO   — calcular_comissao (AtendimentoRealizado); 1 por atendimento, mesmo
              depois de estornado (UNIQUE parcial = idempotencia)
  PAGAMENTO — marcar_paga (baixa de 1 no painel) ou pagar_pendentes (repasse em lote por
              profissional ate uma data, mesmo instante = comprovante); com autor; referencia o CREDITO
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
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from django.db import IntegrityError, transaction
from django.db.models import Case, DecimalField, F, Q, Sum, When
from django.utils import timezone

from ..models import Atendimento, ConsumoSessao, MovimentoComissao, Profissional, RegraComissao
from ..utils.datas import inicio_do_dia

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
        (compra_pacote_item.valor_unitario = valor pago rateado pelo preco de tabela de
        cada procedimento, CompraPacote.congelar_itens) — o valor_cobrado da sessao e o
        preco cheio do procedimento e inflaria a comissao. Avulso: valor_cobrado (cheio
        mesmo com credito de indicacao abatido: o credito e custo de marketing da clinica).
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

        percentual: Optional[Decimal] = None
        if regra.percentual is not None:
            percentual = regra.percentual
            if percentual > CEM:
                # Regra antiga/invalida (>100%) nunca paga mais que o atendimento
                # rendeu; o CHECK/validator do model barra novas.
                logger.warning('regra_comissao_percentual_acima_de_100', extra={'regra_id': regra.pk})
                percentual = CEM
            valor = _centavos(base * percentual / CEM)
        else:
            # valor fixo nunca passa da base (sessao de pacote promocional, avulso com desconto):
            # a clinica nao paga R$ 60 sobre R$ 50 de receita
            valor = regra.valor or ZERO
            if valor > base:
                logger.info('regra_comissao_fixa_limitada_a_base',
                            extra={'regra_id': regra.pk, 'atendimento_id': atendimento.pk})
                valor = base

        if valor <= 0:
            return None

        # Idempotencia por atendimento: no maximo 1 CREDITO ATIVO (nao estornado). Credito novo so
        # depois do estorno do anterior (valor corrigido, realizacao refeita) — no PG o
        # tg_movimento_comissao_validar garante (0063).
        if MovimentoComissao.objects.creditos_ativos().filter(atendimento=atendimento).exists():
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
                    base_calculo=base,              # snapshot: o credito se explica sozinho
                    percentual_aplicado=percentual,
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
    def _derivado(credito: MovimentoComissao, tipo: str, usuario=None, observacoes: str = '', criado_em=None):
        return MovimentoComissao.objects.create(
            tipo=tipo,
            movimento_origem=credito,
            profissional_id=credito.profissional_id,
            atendimento_id=credito.atendimento_id,
            valor=credito.valor,
            usuario_autor=usuario if getattr(usuario, 'pk', None) else None,
            observacoes=observacoes,
            criado_em=criado_em or timezone.now(),
        )

    @staticmethod
    def _estornar_credito_pendente(atendimento: Atendimento, usuario, observacoes: str) -> None:
        """Estorna o credito ativo do atendimento, se houver; PAGO recusa (RegraDeNegocio): o repasse
        ja saiu — o ajuste vai no proximo repasse, fora do sistema."""
        from ..excecoes import RegraDeNegocio

        credito = (MovimentoComissao.objects.select_for_update()
                   .creditos_ativos().filter(atendimento=atendimento).first())
        if credito is None:
            return
        if MovimentoComissao.objects.filter(movimento_origem=credito, tipo=PAGAMENTO).exists():
            raise RegraDeNegocio('A comissão deste atendimento já foi paga à profissional: '
                                 'acerte no próximo repasse.', codigo='comissao_paga')
        ComissaoService._derivado(credito, ESTORNO, usuario, observacoes=observacoes)

    @staticmethod
    @transaction.atomic
    def recalcular(atendimento: Atendimento, usuario=None) -> Optional[MovimentoComissao]:
        """Valor do atendimento REALIZADO corrigido: estorna o credito PENDENTE e lanca o novo com
        a base atual. Comissao ja paga recusa (RegraDeNegocio)."""
        ComissaoService._estornar_credito_pendente(atendimento, usuario, 'valor do atendimento corrigido')
        return ComissaoService.calcular_comissao(atendimento)

    @staticmethod
    @transaction.atomic
    def estornar_para_desfazer(atendimento: Atendimento, usuario=None) -> None:
        """Realizacao desfeita: estorna o credito PENDENTE (pago recusa toda a operacao)."""
        ComissaoService._estornar_credito_pendente(atendimento, usuario, 'realização desfeita')

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
    def pagar_pendentes(profissional_id: int, ate: date, usuario=None) -> list[MovimentoComissao]:
        """Repasse em lote: PAGAMENTO de cada CREDITO PENDENTE da profissional com atendimento ate
        ``ate`` (dia local, inclusive — a data do atendimento, a mesma da competencia do financeiro).

        Uma transacao e um instante so: os PAGAMENTOs do lote tem o mesmo ``criado_em`` (o
        comprovante, ``repasse``, junta o lote por ele — sem tabela de repasse). Cada credito e
        travado e conferido de novo (pagar x estornar concorrentes; no PG o trigger trava outra vez):
        estornado ou ja pago fica de fora. Lista vazia = nada pendente ate a data.
        """
        limite = inicio_do_dia(ate + timedelta(days=1))
        ids = list(
            MovimentoComissao.objects.creditos_com_status()
            .filter(profissional_id=profissional_id, status=MovimentoComissao.STATUS_PENDENTE,
                    atendimento__data_hora_inicio__lt=limite)
            .values_list('pk', flat=True)
        )
        agora = timezone.now()
        observacao = f'repasse até {ate:%d/%m/%Y}'
        pagamentos = []
        for credito in MovimentoComissao.objects.select_for_update().filter(pk__in=ids).order_by('pk'):
            if MovimentoComissao.objects.filter(movimento_origem=credito, tipo__in=(PAGAMENTO, ESTORNO)).exists():
                continue
            pagamentos.append(ComissaoService._derivado(credito, PAGAMENTO, usuario, observacao, criado_em=agora))
        if pagamentos:
            logger.info('comissao_repasse', extra={
                'profissional_id': profissional_id, 'qtd': len(pagamentos),
                'valor': str(sum((p.valor for p in pagamentos), ZERO)),
            })
        return pagamentos

    @staticmethod
    def repasse(pagamento_id: int) -> Optional[dict]:
        """Comprovante do lote de um PAGAMENTO: os PAGAMENTOs da mesma profissional no mesmo
        instante (pagar_pendentes; a baixa individual e um lote de 1), com o credito de cada um."""
        pagamento = (MovimentoComissao.objects.select_related('profissional', 'usuario_autor')
                     .filter(pk=pagamento_id, tipo=PAGAMENTO).first())
        if pagamento is None:
            return None
        lote = list(
            MovimentoComissao.objects.filter(tipo=PAGAMENTO, profissional_id=pagamento.profissional_id,
                                             criado_em=pagamento.criado_em)
            .select_related('movimento_origem', 'movimento_origem__regra',
                            'atendimento__procedimento')
            .order_by('atendimento__data_hora_inicio', 'pk')
        )
        return {
            'pagamento': pagamento,
            'profissional': pagamento.profissional,
            'pago_em': pagamento.criado_em,
            'usuario': pagamento.usuario_autor,
            'observacao': pagamento.observacoes,
            'itens': lote,
            'total': sum((p.valor for p in lote), ZERO),
        }

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
    def ha_regra_ativa() -> bool:
        """Sem nenhuma regra ativa nenhuma comissao e gerada — Comissoes e Financeiro avisam."""
        return RegraComissao.objects.filter(ativo=True).exists()

    @staticmethod
    def totais_por_status(creditos) -> dict:
        """{status: {'total', 'qtd'}} dos CREDITOs (``creditos_com_status()`` ja filtrado), com os
        3 status sempre presentes."""
        totais: dict[str, dict[str, Any]] = {
            status: {'total': ZERO, 'qtd': 0} for status in MovimentoComissao.Status.values
        }
        for status, valor in creditos.order_by().values_list('status', 'valor'):
            totais[status]['total'] += valor
            totais[status]['qtd'] += 1
        return totais

    @staticmethod
    def profissionais_do_filtro():
        """Profissionais do filtro de Comissoes: as ativas e as INATIVAS com saldo a pagar != 0 (quem
        saiu com comissao pendente precisa do repasse)."""
        com_saldo = [pk for pk, saldo in ComissaoService.saldos_por_profissional().items() if saldo]
        return Profissional.objects.filter(Q(ativo=True) | Q(pk__in=com_saldo)).order_by('-ativo', 'nome')

    @staticmethod
    def resumo_por_profissional(creditos, profissional_id: Optional[int] = None) -> list:
        """Linhas do painel por profissional: pendente/paga/qtd dos CREDITOs do filtro
        (``creditos`` = creditos_com_status() ja filtrado) + saldo a pagar do ledger
        inteiro. Profissional sem credito no periodo mas com saldo != 0 tambem aparece
        (saldo negativo nao some do painel)."""
        linhas: dict[int, dict[str, Any]] = {}
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
            linha['gerada'] = linha['pendente'] + linha['paga']
            linha['saldo'] = saldos.get(linha['profissional_id'], ZERO)
            linha['saldo_negativo'] = linha['saldo'] < 0
        return sorted(linhas.values(), key=lambda r: (-r['pendente'], r['profissional__nome'] or ''))

