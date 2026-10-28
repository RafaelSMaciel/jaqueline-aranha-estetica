"""Comissao explicavel e repasse (decisoes "regras de comissao" e "repasse de comissao").

- O CREDITO congela a base e o percentual aplicados (0066).
- Regra ja usada nao muda (so desativa); a tela Comissoes > Regras cria, desativa e exclui a nunca usada.
- Repasse em lote: "pagar pendentes ate dd/mm" lanca N PAGAMENTOs numa transacao, no mesmo instante
  (comprovante), auditado; credito estornado nao e pago.
- A profissional ve "Minhas comissoes" (so as dela, sem nome de cliente).
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import Atendimento, LogAuditoria, MovimentoComissao, RegraComissao, Usuario
from aranha_estetica.services.comissao import ComissaoService
from aranha_estetica.utils.datas import data_local

from .factories import criar_atendimento, criar_cliente, criar_comissao, criar_procedimento, criar_profissional, \
    ja_comecou


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class _Base(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente('Clara Cliente')
        self.admin = Usuario.objects.create_user(email='adm@comissao.test', password='x', papel=Usuario.PAPEL_ADMIN)

    def _realizado(self, dias=1, valor='200.00', prof=None):
        at = criar_atendimento(self.cli, prof or self.prof, self.proc,
                               data_hora=timezone.now() - timedelta(days=dias), status='AGENDADO')
        Atendimento.objects.filter(pk=at.pk).update(valor_cobrado=Decimal(valor))
        at.refresh_from_db()
        ja_comecou(at).marcar_realizado()      # a FSM lanca o CREDITO
        return at


class SnapshotDoCreditoTests(_Base):
    def test_credito_grava_base_e_percentual(self):
        RegraComissao.objects.create(percentual=Decimal('30'), ativo=True)
        credito = MovimentoComissao.objects.get(atendimento=self._realizado(valor='150.00'), tipo='CREDITO')
        self.assertEqual((credito.base_calculo, credito.percentual_aplicado, credito.valor),
                         (Decimal('150.00'), Decimal('30.00'), Decimal('45.00')))

    def test_regra_fixa_grava_base_sem_percentual(self):
        RegraComissao.objects.create(valor=Decimal('80.00'), ativo=True)
        credito = MovimentoComissao.objects.get(atendimento=self._realizado(valor='50.00'), tipo='CREDITO')
        # fixo limitado a base (nunca paga mais que o atendimento rendeu)
        self.assertEqual((credito.base_calculo, credito.percentual_aplicado, credito.valor),
                         (Decimal('50.00'), None, Decimal('50.00')))

    def test_derivados_sem_snapshot(self):
        RegraComissao.objects.create(percentual=Decimal('10'), ativo=True)
        credito = MovimentoComissao.objects.get(atendimento=self._realizado(), tipo='CREDITO')
        pagamento = ComissaoService.marcar_paga(credito.pk)
        self.assertEqual((pagamento.base_calculo, pagamento.percentual_aplicado), (None, None))


class RegraImutavelDepoisDeUsadaTests(_Base):
    def test_regra_usada_nao_muda_so_desativa(self):
        regra = RegraComissao.objects.create(percentual=Decimal('30'), ativo=True)
        self._realizado()
        regra.percentual = Decimal('40')
        with self.assertRaises(ValidationError):
            regra.save()
        regra.refresh_from_db()
        regra.ativo = False
        regra.save()
        self.assertFalse(RegraComissao.objects.get(pk=regra.pk).ativo)

    def test_regra_nunca_usada_pode_mudar(self):
        regra = RegraComissao.objects.create(percentual=Decimal('30'), ativo=True)
        regra.percentual = Decimal('35')
        regra.save()
        self.assertEqual(RegraComissao.objects.get(pk=regra.pk).percentual, Decimal('35.00'))

    def test_django_admin_somente_leitura_para_regra_usada(self):
        from django.contrib import admin
        from django.test import RequestFactory

        regra = RegraComissao.objects.create(percentual=Decimal('30'), ativo=True)
        nova = RegraComissao.objects.create(percentual=Decimal('20'), ativo=False)
        self._realizado()
        request = RequestFactory().get('/')
        request.user = self.admin
        modeladmin = admin.site._registry[RegraComissao]
        self.assertEqual(set(modeladmin.get_readonly_fields(request, regra)),
                         {'profissional', 'procedimento', 'percentual', 'valor'})
        self.assertFalse(modeladmin.has_delete_permission(request, regra))
        self.assertEqual(tuple(modeladmin.get_readonly_fields(request, nova)), ())


class TelaDeRegrasTests(_Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_criar_regra_percentual_ou_fixo(self):
        url = reverse('aranha:admin_comissao_regra_criar')
        for dados in ({}, {'percentual': '10', 'valor': '50'}, {'percentual': '101'}, {'valor': '0'}):
            with self.subTest(dados=dados):
                resp = self.client.post(url, dados)
                self.assertTrue(_mensagens(resp))
        self.assertFalse(RegraComissao.objects.exists())
        self.client.post(url, {'profissional': str(self.prof.pk), 'percentual': '27,5'})
        regra = RegraComissao.objects.get()
        self.assertEqual((regra.profissional_id, regra.percentual, regra.valor, regra.ativo),
                         (self.prof.pk, Decimal('27.50'), None, True))
        self.assertEqual(regra.get_especificidade_display(), 'Profissional (qualquer procedimento)')
        self.assertTrue(LogAuditoria.objects.filter(acao='Criou regra de comissão', registro_id=regra.pk).exists())

    def test_lista_mostra_usos_e_avisa_sem_regra_ativa(self):
        resp = self.client.get(reverse('aranha:painel_comissao_regras'))
        self.assertContains(resp, 'Nenhuma regra ativa')
        regra = RegraComissao.objects.create(percentual=Decimal('30'), ativo=True)
        self._realizado()
        resp = self.client.get(reverse('aranha:painel_comissao_regras'))
        self.assertNotContains(resp, 'Nenhuma regra ativa')
        self.assertEqual([(r.pk, r.usos) for r in resp.context['regras']], [(regra.pk, 1)])

    def test_desativar_e_excluir(self):
        usada = RegraComissao.objects.create(percentual=Decimal('30'), ativo=True)
        self._realizado()
        nunca = RegraComissao.objects.create(procedimento=self.proc, valor=Decimal('20'), ativo=True)
        self.client.post(reverse('aranha:admin_comissao_regra_ativo', args=[usada.pk]))
        self.assertFalse(RegraComissao.objects.get(pk=usada.pk).ativo)
        resp = self.client.post(reverse('aranha:admin_comissao_regra_excluir', args=[usada.pk]))
        self.assertIn('Regra já usada em comissão não é excluída: desative-a.', _mensagens(resp))
        self.assertTrue(RegraComissao.objects.filter(pk=usada.pk).exists())
        self.client.post(reverse('aranha:admin_comissao_regra_excluir', args=[nunca.pk]))
        self.assertFalse(RegraComissao.objects.filter(pk=nunca.pk).exists())

    def test_tela_de_comissoes_avisa_sem_regra(self):
        self.assertContains(self.client.get(reverse('aranha:painel_comissoes')), 'Nenhuma regra de comissão ativa')


class RepasseEmLoteTests(_Base):
    def setUp(self):
        super().setUp()
        RegraComissao.objects.create(percentual=Decimal('10'), ativo=True)
        self.a1, self.a2 = self._realizado(dias=5), self._realizado(dias=3)
        self.recente = self._realizado(dias=1)
        self.outra_prof = criar_profissional('Dra. Outra')
        self.da_outra = self._realizado(dias=4, prof=self.outra_prof)

    def _credito(self, at):
        return MovimentoComissao.objects.get(atendimento=at, tipo='CREDITO')

    def test_paga_pendentes_ate_a_data_numa_transacao_e_mesmo_instante(self):
        ja_paga = self._credito(self.a2)
        ComissaoService.marcar_paga(ja_paga.pk)
        ate = data_local(self.a1.data_hora_inicio) + timedelta(days=2)    # a1 sim; a2 ja paga; recente nao
        pagamentos = ComissaoService.pagar_pendentes(self.prof.pk, ate, usuario=self.admin)
        self.assertEqual([p.movimento_origem_id for p in pagamentos], [self._credito(self.a1).pk])
        self.assertEqual(self._credito(self.recente).status, 'PENDENTE')
        self.assertEqual(self._credito(self.da_outra).status, 'PENDENTE')
        # ate hoje: so falta o recente (a1 saiu no lote anterior, a2 ja estava paga)
        lote = ComissaoService.pagar_pendentes(self.prof.pk, timezone.localdate(), usuario=self.admin)
        self.assertEqual(len(lote), 1)
        self.assertEqual(ComissaoService.pagar_pendentes(self.prof.pk, timezone.localdate()), [])

    def test_lote_de_varios_tem_o_mesmo_instante_e_comprovante(self):
        lote = ComissaoService.pagar_pendentes(self.prof.pk, timezone.localdate(), usuario=self.admin)
        self.assertEqual(len(lote), 3)
        self.assertEqual(len({p.criado_em for p in lote}), 1)
        repasse = ComissaoService.repasse(lote[0].pk)
        self.assertEqual((len(repasse['itens']), repasse['total']), (3, Decimal('60.00')))
        self.assertEqual(ComissaoService.saldo_a_pagar(self.prof), Decimal('0.00'))

    def test_credito_estornado_nao_e_pago(self):
        estornado = self._credito(self.a1)
        ComissaoService.estornar_para_desfazer(self.a1)
        lote = ComissaoService.pagar_pendentes(self.prof.pk, timezone.localdate())
        self.assertNotIn(estornado.pk, [p.movimento_origem_id for p in lote])
        self.assertEqual(len(lote), 2)

    def test_tela_repasse_audita_e_abre_comprovante(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('aranha:admin_comissao_repasse'),
                                {'profissional': str(self.prof.pk), 'ate': timezone.localdate().isoformat()})
        pagamento = MovimentoComissao.objects.filter(tipo='PAGAMENTO').order_by('pk').first()
        self.assertRedirects(resp, reverse('aranha:painel_comissao_repasse', args=[pagamento.pk]))
        log = LogAuditoria.objects.get(acao='Pagou comissões em lote (repasse)')
        self.assertEqual((log.detalhes['qtd'], log.detalhes['valor']), (3, '60.00'))
        comprovante = self.client.get(resp['Location'])
        self.assertContains(comprovante, 'R$ 60,00')
        self.assertNotContains(comprovante, 'Clara Cliente')   # comprovante sem nome de cliente
        # data futura: recusada; profissional sem pendencia: aviso
        resp = self.client.post(reverse('aranha:admin_comissao_repasse'), {
            'profissional': str(self.prof.pk), 'ate': (timezone.localdate() + timedelta(days=1)).isoformat()})
        self.assertTrue(any('até hoje' in m for m in _mensagens(resp)))
        resp = self.client.post(reverse('aranha:admin_comissao_repasse'),
                                {'profissional': str(self.prof.pk), 'ate': timezone.localdate().isoformat()})
        self.assertRedirects(resp, reverse('aranha:painel_comissoes'))
        self.assertEqual(self.client.get(reverse('aranha:painel_comissao_repasse', args=[999999])).status_code, 404)

    def test_filtro_inclui_profissional_inativa_com_saldo(self):
        self.outra_prof.ativo = False
        self.outra_prof.save(update_fields=['ativo'])
        self.assertIn(self.outra_prof, list(ComissaoService.profissionais_do_filtro()))
        ComissaoService.pagar_pendentes(self.outra_prof.pk, timezone.localdate())
        self.assertNotIn(self.outra_prof, list(ComissaoService.profissionais_do_filtro()))


class MinhasComissoesTests(_Base):
    def test_profissional_ve_so_as_dela_sem_nome_de_cliente(self):
        RegraComissao.objects.create(percentual=Decimal('10'), ativo=True)
        minha = self._realizado(dias=0)
        outra = criar_profissional('Dra. Outra')
        self._realizado(dias=0, valor='999.00', prof=outra)
        usuario = Usuario.objects.create_user(email='prof@comissao.test', password='x',
                                              papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof)
        self.client.force_login(usuario)
        resp = self.client.get(reverse('aranha:profissional_comissoes'),
                               {'mes': data_local(minha.data_hora_inicio).strftime('%Y-%m')})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([c.atendimento_id for c in resp.context['creditos']], [minha.pk])
        self.assertEqual(resp.context['saldo_a_receber'], Decimal('20.00'))
        self.assertNotContains(resp, 'Clara Cliente')
        self.assertNotContains(resp, 'R$ 99,90')
        self.assertContains(self.client.get(reverse('aranha:profissional_agenda')), 'Minhas comissões')

    def test_credito_antigo_sem_snapshot_aparece(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, data_hora=timezone.now() - timedelta(hours=3),
                               status='REALIZADO')
        criar_comissao(at, valor='15.00')
        usuario = Usuario.objects.create_user(email='prof2@comissao.test', password='x',
                                              papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof)
        self.client.force_login(usuario)
        resp = self.client.get(reverse('aranha:profissional_comissoes'),
                               {'mes': data_local(at.data_hora_inicio).strftime('%Y-%m')})
        self.assertContains(resp, 'R$ 15,00')
