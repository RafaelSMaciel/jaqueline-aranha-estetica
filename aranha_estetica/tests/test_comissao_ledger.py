"""Comissao como livro-razao (docs/specs/padrao-sql.md §4.f, migration 0053) e os
achados §7.3 itens 1-3 (cashback sem nome, ticket medio unico, LTV sem dupla contagem).

No SQLite o Python cobre os triggers (save/update/delete recusados, validacao do
lancamento); o PG fica em test_pg_ddl.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import (
    Atendimento, ConsumoSessao, LogAuditoria, MovimentoCarteira, MovimentoComissao, RegraComissao, Usuario,
)
from aranha_estetica.services import financeiro
from aranha_estetica.services.comissao_service import ComissaoService
from aranha_estetica.services.fidelidade_service import FidelidadeService

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_comissao,
    criar_compra_pacote,
    criar_pacote,
    criar_procedimento,
    criar_profissional,
)

C, P, E = (MovimentoComissao.TIPO_CREDITO, MovimentoComissao.TIPO_PAGAMENTO, MovimentoComissao.TIPO_ESTORNO)


class _Base(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()
        self.admin = Usuario.objects.create_user(email='adm@comissao.com', password='x', papel=Usuario.PAPEL_ADMIN)
        RegraComissao.objects.create(percentual=Decimal('10'), ativo=True)

    def _realizado(self, valor='200.00', dias=1):
        at = criar_atendimento(self.cli, self.prof, self.proc, status='AGENDADO',
                               data_hora=timezone.now() - timedelta(days=dias))
        Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal(valor))
        at.refresh_from_db()
        return at

    def _tipos(self, atendimento):
        return sorted(MovimentoComissao.objects.filter(atendimento=atendimento).values_list('tipo', flat=True))


class LedgerComissaoTests(_Base):
    def test_credito_idempotente_mesmo_depois_de_estornado(self):
        at = self._realizado()
        credito = ComissaoService.calcular_comissao(at)
        self.assertEqual((credito.tipo, credito.valor, credito.status), (C, Decimal('20.00'), 'PENDENTE'))
        self.assertIsNone(ComissaoService.calcular_comissao(at))
        self.assertEqual(ComissaoService.estornar_comissao(at), 1)
        self.assertIsNone(ComissaoService.calcular_comissao(at))  # nunca um 2o CREDITO
        self.assertEqual(self._tipos(at), [C, E])

    def test_pagar_lanca_pagamento_com_autor_e_nao_paga_duas_vezes(self):
        at = self._realizado()
        credito = ComissaoService.calcular_comissao(at)
        pagamento = ComissaoService.marcar_paga(credito.pk, usuario=self.admin)
        self.assertEqual((pagamento.tipo, pagamento.valor, pagamento.movimento_origem_id, pagamento.usuario_autor_id),
                         (P, credito.valor, credito.pk, self.admin.pk))
        self.assertIsNone(ComissaoService.marcar_paga(credito.pk))
        self.assertIsNone(ComissaoService.marcar_paga(pagamento.pk))  # so CREDITO se paga
        credito.refresh_from_db()
        self.assertEqual((credito.status, credito.pago_em), ('PAGA', pagamento.criado_em))
        self.assertEqual(self._tipos(at), [C, P])

    def test_estornado_nao_pode_ser_pago(self):
        at = self._realizado()
        credito = ComissaoService.calcular_comissao(at)
        ComissaoService.estornar_comissao(at, usuario=self.admin)
        self.assertIsNone(ComissaoService.marcar_paga(credito.pk))
        self.assertEqual(ComissaoService.estornar_comissao(at), 0)  # idempotente
        self.assertEqual(MovimentoComissao.objects.get(tipo=E).usuario_autor_id, self.admin.pk)
        self.assertEqual(MovimentoComissao.objects.creditos_com_status().get().status, 'ESTORNADA')

    def test_pago_e_estornado_deixa_saldo_negativo(self):
        a1, a2 = self._realizado('200.00', dias=2), self._realizado('300.00', dias=1)
        c1, c2 = ComissaoService.calcular_comissao(a1), ComissaoService.calcular_comissao(a2)
        self.assertEqual(ComissaoService.saldo_a_pagar(self.prof), Decimal('50.00'))
        ComissaoService.marcar_paga(c1.pk, usuario=self.admin)
        ComissaoService.marcar_paga(c2.pk, usuario=self.admin)
        self.assertEqual(ComissaoService.saldo_a_pagar(self.prof), Decimal('0.00'))
        ComissaoService.estornar_comissao(a2)  # ja paga: a clinica pagou 30 a mais
        self.assertEqual(ComissaoService.saldo_a_pagar(self.prof), Decimal('-30.00'))
        status = dict(MovimentoComissao.objects.creditos_com_status().values_list('pk', 'status'))
        self.assertEqual(status, {c1.pk: 'PAGA', c2.pk: 'ESTORNADA'})
        # o proximo credito desconta a divida
        c3 = ComissaoService.calcular_comissao(self._realizado('500.00', dias=0))
        self.assertEqual(c3.valor, Decimal('50.00'))
        self.assertEqual(ComissaoService.saldo_a_pagar(self.prof), Decimal('20.00'))

    def test_append_only_no_python(self):
        credito = criar_comissao(self._realizado(), valor='20.00')
        credito.valor = Decimal('99.00')
        for acao in (credito.save, credito.delete,
                     lambda: MovimentoComissao.objects.filter(pk=credito.pk).update(valor=1),
                     lambda: MovimentoComissao.objects.filter(pk=credito.pk).delete()):
            with self.subTest(acao=acao), self.assertRaises(ValidationError):
                acao()
        credito.refresh_from_db()
        self.assertEqual(credito.valor, Decimal('20.00'))

    def test_validacao_do_lancamento_espelha_o_trigger(self):
        at = self._realizado()
        credito = criar_comissao(at, valor='20.00')
        base = {'profissional': self.prof, 'atendimento': at, 'movimento_origem': credito}
        with self.assertRaisesMessage(ValidationError, 'sem baixa parcial'):
            MovimentoComissao.objects.create(tipo=P, valor=Decimal('10.00'), **base)
        outro = criar_profissional('Dra. Bia')
        with self.assertRaisesMessage(ValidationError, 'repetir profissional'):
            MovimentoComissao.objects.create(tipo=P, valor=Decimal('20.00'), **{**base, 'profissional': outro})
        estorno = MovimentoComissao.objects.create(tipo=E, valor=Decimal('20.00'), **base)
        with self.assertRaisesMessage(ValidationError, 'estornada não pode ser paga'):
            MovimentoComissao.objects.create(tipo=P, valor=Decimal('20.00'), **base)
        with self.assertRaisesMessage(ValidationError, 'CRÉDITO'):
            MovimentoComissao.objects.create(tipo=E, valor=Decimal('20.00'), **{**base, 'movimento_origem': estorno})

    def test_constraints_do_ledger_no_banco(self):
        at = self._realizado()
        credito = criar_comissao(at, valor='20.00')
        casos = {
            'CREDITO repetido': dict(tipo=C, valor=Decimal('5')),
            'valor zero': dict(tipo=C, valor=Decimal('0'), atendimento=self._realizado(dias=3)),
            'credito com origem': dict(tipo=C, valor=Decimal('5'), movimento_origem=credito,
                                       atendimento=self._realizado(dias=4)),
            'pagamento sem origem': dict(tipo=P, valor=Decimal('20.00')),
            'pagamento com regra': dict(tipo=P, valor=Decimal('20.00'), movimento_origem=credito,
                                        regra=RegraComissao.objects.get()),
            'tipo invalido': dict(tipo='OUTRO', valor=Decimal('20.00'), movimento_origem=credito),
        }
        for nome, campos in casos.items():
            dados = {'profissional': self.prof, 'atendimento': at, **campos}
            with self.subTest(nome), self.assertRaises(IntegrityError), transaction.atomic():
                # bulk_create pula o save() (validacao Python): o banco tem de barrar sozinho
                MovimentoComissao.objects.bulk_create([MovimentoComissao(**dados)])
        MovimentoComissao.objects.create(tipo=P, valor=Decimal('20.00'), profissional=self.prof, atendimento=at,
                                         movimento_origem=credito)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovimentoComissao.objects.bulk_create([MovimentoComissao(
                tipo=P, valor=Decimal('20.00'), profissional=self.prof, atendimento=at, movimento_origem=credito)])

    def test_cancelamento_pela_fsm_estorna(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, status='AGENDADO')
        Atendimento.objects.filter(pk=at.pk).update(valor_cobrado=Decimal('100.00'))
        at.refresh_from_db()
        criar_comissao(at, valor='10.00', status='PAGA')  # legado: comissao num agendado
        with self.captureOnCommitCallbacks(execute=True):
            at.cancelar(motivo='Cliente Fulana desistiu', by_user=self.admin)
        self.assertEqual(self._tipos(at), [C, E, P])
        estorno = MovimentoComissao.objects.get(tipo=E)
        self.assertNotIn('Fulana', estorno.observacoes)  # texto livre nao vai p/ o ledger imutavel


class RegraUsadaNaoSeExcluiTests(_Base):
    def test_protect(self):
        from django.db.models import ProtectedError

        regra = RegraComissao.objects.get()
        ComissaoService.calcular_comissao(self._realizado())
        with self.assertRaises(ProtectedError):
            regra.delete()
        regra.ativo = False
        regra.save()
        self.assertIsNone(ComissaoService.resolver_regra(self.prof.pk, self.proc.pk))


class PainelComissoesLedgerTests(_Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_painel_mostra_status_derivado_e_saldo_negativo(self):
        pend = criar_comissao(self._realizado(dias=3), valor='10.00')
        criar_comissao(self._realizado(dias=2), valor='20.00', status='PAGA')
        criar_comissao(self._realizado(dias=1), valor='40.00', status='PAGA_ESTORNADA')
        resp = self.client.get(reverse('aranha:painel_comissoes'), {'periodo': 'tudo'})
        ctx = resp.context
        self.assertEqual((ctx['pendente_total'], ctx['pendente_count']), (Decimal('10.00'), 1))
        self.assertEqual((ctx['paga_total'], ctx['paga_count']), (Decimal('20.00'), 1))
        self.assertEqual(ctx['estornada_count'], 1)
        linha = ctx['por_profissional'][0]
        # 10 + 20 + 40 creditados − 20 − 40 pagos − 40 estornado = −30
        self.assertEqual((linha['pendente'], linha['paga'], linha['qtd'], linha['saldo']),
                         (Decimal('10.00'), Decimal('20.00'), 2, Decimal('-30.00')))
        self.assertTrue(ctx['tem_saldo_negativo'])
        self.assertContains(resp, 'a descontar do próximo repasse')
        self.assertContains(resp, f'/painel/comissoes/{pend.pk}/pagar/')
        resp = self.client.get(reverse('aranha:painel_comissoes'), {'periodo': 'tudo', 'status': 'ESTORNADA'})
        self.assertEqual([c.status for c in resp.context['comissoes']], ['ESTORNADA'])

    def test_profissional_so_com_divida_aparece_no_resumo(self):
        # credito de 200 dias atras (fora do periodo de 30 dias), pago e estornado
        criar_comissao(self._realizado(dias=200), valor='40.00', status='PAGA_ESTORNADA',
                       criado_em=timezone.now() - timedelta(days=200))
        resp = self.client.get(reverse('aranha:painel_comissoes'), {'periodo': '30'})
        self.assertEqual([(r['qtd'], r['saldo']) for r in resp.context['por_profissional']],
                         [(0, Decimal('-40.00'))])

    def test_baixa_pelo_painel_grava_autor_e_auditoria(self):
        credito = criar_comissao(self._realizado(), valor='10.00')
        resp = self.client.post(reverse('aranha:admin_comissao_pagar', args=[credito.pk]))
        self.assertEqual(resp.status_code, 302)
        pagamento = MovimentoComissao.objects.get(tipo=P)
        self.assertEqual(pagamento.usuario_autor_id, self.admin.pk)
        log = LogAuditoria.objects.get(acao='Marcou comissão como paga')
        self.assertEqual((log.registro_id, log.detalhes['pagamento']), (credito.pk, pagamento.pk))

    def test_financeiro_pendentes_e_valor_a_descontar(self):
        criar_comissao(self._realizado(dias=2), valor='10.00')
        criar_comissao(self._realizado(dias=1), valor='40.00', status='PAGA_ESTORNADA')
        from django.core.cache import cache
        cache.clear()
        ctx = self.client.get(reverse('aranha:dashboard_financeiro')).context
        self.assertEqual((ctx['comissoes_pendentes_total'], ctx['comissoes_pendentes_count']), (Decimal('10.00'), 1))
        # saldo = 10 + 40 creditados − 40 pago − 40 estornado = −30
        self.assertEqual(ctx['comissoes_a_descontar'], Decimal('30.00'))
        self.assertEqual([(r['total'], r['count']) for r in ctx['comissoes_por_prof']], [(Decimal('10.00'), 1)])

    def test_admin_somente_leitura_com_tipo(self):
        from django.contrib import admin as django_admin
        from django.test import RequestFactory

        rf = RequestFactory().get('/')
        rf.user = self.admin
        ma = django_admin.site._registry[MovimentoComissao]
        credito = criar_comissao(self._realizado(), valor='10.00', status='PAGA')
        self.assertFalse(ma.has_add_permission(rf))
        self.assertFalse(ma.has_change_permission(rf, credito))
        self.assertFalse(ma.has_delete_permission(rf, credito))
        self.assertIn('tipo', ma.list_display)


# ─── §7.3 item 1: nome da indicada fora do ledger da carteira ──────────────
class CashbackSemNomeNoLedgerTests(_Base):
    def test_observacao_do_cashback_so_tem_o_id(self):
        indicadora = criar_cliente('Indicadora Ana')
        indicada = criar_cliente('Indicada Beatriz Sigilosa', indicado_por=indicadora)
        at = criar_atendimento(indicada, self.prof, self.proc, status='AGENDADO')
        Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal('150.00'))
        at.refresh_from_db()
        mov = FidelidadeService.liberar_cashback_indicacao(at)
        self.assertEqual(mov.observacoes, f'Indicacao (cli #{indicada.pk})')
        self.assertFalse(MovimentoCarteira.objects.filter(observacoes__contains='Beatriz').exists())


# ─── §7.3 itens 2 e 3: ticket medio unico e LTV sem dupla contagem ─────────
class FaturamentoUnificadoTests(_Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        # adiante de "agora": cai no mes corrente E nos ultimos 30 dias (as duas telas
        # so tem limite inferior) — horarios em sequencia (EXCLUDE do PG)
        self.quando = timezone.now() + timedelta(hours=1)

    def _feito(self, valor, cliente=None, **campos):
        at = criar_atendimento(cliente or self.cli, self.prof, self.proc, status='AGENDADO', data_hora=self.quando)
        Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal(valor), **campos)
        self.quando += timedelta(minutes=self.proc.duracao_minutos)
        return at

    def test_ticket_medio_igual_no_dashboard_no_financeiro_e_na_ficha(self):
        from django.core.cache import cache

        self._feito('200.00')
        self._feito('101.00')
        self._feito('0')                                     # sem valor: fora do ticket
        origem = self._feito('150.00')
        self._feito('0', eh_retorno=True, atendimento_origem=origem)  # retorno: fora
        compra = criar_compra_pacote(self.cli, criar_pacote(preco=Decimal('600.00'), procedimento=self.proc, sessoes=4))
        sessao = criar_atendimento(self.cli, self.prof, self.proc, status='AGENDADO', data_hora=self.quando)
        ConsumoSessao.objects.create(compra_pacote=compra, atendimento=sessao)
        Atendimento.objects.filter(pk=sessao.pk).update(status='REALIZADO', valor_cobrado=Decimal('999.00'))

        esperado = financeiro.ticket_medio(Decimal('451.00'), 3)
        self.assertEqual(esperado, Decimal('150.33'))
        overview = self.client.get(reverse('aranha:painel_overview')).context
        cache.clear()
        fin = self.client.get(reverse('aranha:dashboard_financeiro')).context
        ficha = self.client.get(reverse('aranha:admin_cliente_detalhe', args=[self.cli.pk])).context
        self.assertEqual(overview['ticket_medio'], '150,33')
        self.assertEqual(fin['ticket_medio_mes'], esperado)
        self.assertEqual(ficha['cliente_stats']['ticket_medio'], esperado)
        self.assertEqual(overview['receita_mensal'], '1.051,00')  # 451 avulsos + 600 do pacote

    def test_ltv_da_ficha_nao_soma_sessao_de_pacote_a_preco_cheio(self):
        self._feito('200.00')
        compra = criar_compra_pacote(self.cli, criar_pacote(preco=Decimal('600.00'), procedimento=self.proc, sessoes=4))
        for _ in range(2):
            sessao = criar_atendimento(self.cli, self.prof, self.proc, status='AGENDADO', data_hora=self.quando)
            self.quando += timedelta(minutes=self.proc.duracao_minutos)
            ConsumoSessao.objects.create(compra_pacote=compra, atendimento=sessao)
            Atendimento.objects.filter(pk=sessao.pk).update(status='REALIZADO', valor_cobrado=Decimal('150.00'))
        # pacote cancelado com reembolso parcial: entra o liquido
        outra = criar_compra_pacote(self.cli, criar_pacote('Outro', preco=Decimal('300.00'), procedimento=self.proc))
        type(outra).objects.filter(pk=outra.pk).update(status='CANCELADO', valor_reembolsado=Decimal('250.00'))
        stats = self.client.get(reverse('aranha:admin_cliente_detalhe', args=[self.cli.pk])).context['cliente_stats']
        # 200 avulso + 600 pacote + 50 retido; as 2 sessoes (300 a preco cheio) nao somam
        self.assertEqual((stats['ltv'], stats['ltv_avulsos'], stats['ltv_pacotes']),
                         (Decimal('850.00'), Decimal('200.00'), Decimal('650.00')))
        self.assertEqual(stats['qtd_realizados'], 3)
        self.assertEqual(financeiro.valor_gasto_cliente(self.cli)['total'], Decimal('850.00'))
