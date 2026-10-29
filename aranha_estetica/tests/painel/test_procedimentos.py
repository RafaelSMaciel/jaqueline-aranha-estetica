"""Procedimentos: ProcedimentoForm (ModelForm), services/catalogo (preco base versionado e quem realiza) e
a lista do painel com o preco pela mesma regra do site (utils/precos.preco_base_map)."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.excecoes import RegraDeNegocio
from aranha_estetica.forms.cadastros import ProcedimentoForm
from aranha_estetica.models import Habilitacao, Preco, Procedimento, Usuario
from aranha_estetica.services import catalogo
from aranha_estetica.utils.precos import preco_base_map

from ..factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional


class PrecoBaseTests(TestCase):
    def setUp(self):
        self.proc = criar_procedimento(preco=None)
        self.hoje = timezone.localdate()
        Preco.objects.create(procedimento=self.proc, valor=Decimal('180.00'), vigente_desde=self.hoje - timedelta(days=10))

    def test_preco_igual_nao_cria_vigencia(self):
        self.assertFalse(catalogo.definir_preco_base(self.proc, Decimal('180.00')))
        self.assertFalse(catalogo.definir_preco_base(self.proc, None))
        self.assertEqual(Preco.objects.filter(procedimento=self.proc).count(), 1)

    def test_preco_alterado_cria_vigencia_hoje_e_ajusta_a_de_hoje(self):
        self.assertTrue(catalogo.definir_preco_base(self.proc, Decimal('199.90')))
        atual = Preco.objects.get(procedimento=self.proc, vigente_desde=self.hoje)
        self.assertEqual((atual.valor, atual.profissional_id), (Decimal('199.90'), None))
        self.assertTrue(catalogo.definir_preco_base(self.proc, Decimal('210.00')))
        self.assertEqual(Preco.objects.filter(procedimento=self.proc).count(), 2)   # a de hoje foi ajustada
        self.assertEqual(preco_base_map([self.proc])[self.proc.pk], Decimal('210.00'))

    def test_so_preco_por_profissional_compara_com_o_que_o_site_mostra(self):
        proc = criar_procedimento('Drenagem', preco=None)
        prof = criar_profissional('Dra. Lia')
        Preco.objects.create(procedimento=proc, profissional=prof, valor=Decimal('90.00'),
                             vigente_desde=self.hoje - timedelta(days=1))
        self.assertFalse(catalogo.definir_preco_base(proc, Decimal('90.00')))   # salvar sem mexer
        self.assertFalse(Preco.objects.filter(procedimento=proc, profissional__isnull=True).exists())


class SincronizarHabilitacoesTests(TestCase):
    def test_nao_tira_quem_tem_atendimento_futuro(self):
        ana, bia = criar_profissional('Ana'), criar_profissional('Bia')
        proc = criar_procedimento('Toxina', profissional=ana)
        criar_atendimento(criar_cliente(), ana, proc)
        with self.assertRaises(RegraDeNegocio) as ctx:
            catalogo.sincronizar_habilitacoes(proc, [bia])
        self.assertEqual(ctx.exception.codigo, 'habilitacao_em_uso')
        self.assertIn('Ana ainda tem 1 atendimento(s) futuro(s) de Toxina', ctx.exception.mensagem)
        catalogo.sincronizar_habilitacoes(proc, [ana, bia])
        self.assertEqual(Habilitacao.objects.filter(procedimento=proc).count(), 2)


class ProcedimentoFormTests(TestCase):
    def test_e_model_form_com_ativo_booleano(self):
        proc = criar_procedimento('Peeling', duracao=45, categoria='FACIAL')
        form = ProcedimentoForm({'nome': 'Peeling Glow', 'ativo': 'on'}, instance=proc)
        self.assertTrue(form.is_valid(), form.errors)
        salvo = form.save()
        self.assertEqual((salvo.pk, salvo.nome, salvo.duracao_minutos, salvo.categoria, salvo.ativo),
                         (proc.pk, 'Peeling Glow', 45, 'FACIAL', True))
        form = ProcedimentoForm({'nome': 'Peeling Glow'}, instance=proc)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.save().ativo)   # checkbox ausente = inativo (como antes)


class ListaProcedimentosTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(email='dona-proc@test.com', password='senha-forte-123',
                                                      nome='Dona')
        self.client.force_login(self.admin)
        self.hoje = timezone.localdate()

    def test_lista_mostra_o_mesmo_preco_do_site(self):
        so_profissional = criar_procedimento('Drenagem', preco=None)
        Preco.objects.create(procedimento=so_profissional, profissional=criar_profissional('Dra. Lia'),
                             valor=Decimal('95.00'), vigente_desde=self.hoje - timedelta(days=2))
        so_futuro = criar_procedimento('Bioestimulador', preco=None)
        Preco.objects.create(procedimento=so_futuro, valor=Decimal('1200.00'), vigente_desde=self.hoje + timedelta(days=5))
        resp = self.client.get(reverse('aranha:admin_procedimentos'))
        site = preco_base_map([so_profissional, so_futuro])
        na_lista = {p.pk: p.preco_base for p in resp.context['procedimentos']}
        self.assertEqual(na_lista[so_profissional.pk], site[so_profissional.pk])
        self.assertEqual(na_lista[so_futuro.pk], site[so_futuro.pk])
        self.assertContains(resp, 'value="95.00"')

    def test_preco_e_habilitacoes_calculados_na_pagina_aberta(self):
        prof = criar_profissional('Dra. Ana')
        for i in range(32):
            proc = Procedimento.objects.create(nome=f'Proc {i:02d}', duracao_minutos=30, categoria='FACIAL', ativo=True)
            Preco.objects.create(procedimento=proc, valor=Decimal(100 + i), vigente_desde=self.hoje)
            Habilitacao.objects.create(procedimento=proc, profissional=prof)
        resp = self.client.get(reverse('aranha:admin_procedimentos'), {'page': 2})
        pagina = resp.context['procedimentos']
        self.assertEqual((pagina.number, len(pagina.object_list)), (2, 2))
        self.assertEqual([p.preco_base for p in pagina], [Decimal('130.00'), Decimal('131.00')])
        self.assertTrue(all(p.habilitados == {prof.pk} for p in pagina))

    def test_editar_com_o_mesmo_preco_nao_cria_vigencia_e_com_outro_cria_hoje(self):
        proc = criar_procedimento('Massagem', preco=None)
        Preco.objects.create(procedimento=proc, valor=Decimal('150.00'), vigente_desde=self.hoje - timedelta(days=30))
        url = reverse('aranha:admin_editar_procedimento', args=[proc.pk])
        dados = {'nome': 'Massagem', 'duracao_minutos': '60', 'categoria': 'CORPORAL', 'ativo': '1'}
        self.assertEqual(self.client.post(url, {**dados, 'preco': '150,00'}).status_code, 302)
        self.assertEqual(Preco.objects.filter(procedimento=proc).count(), 1)
        self.client.post(url, {**dados, 'preco': '165'})
        self.assertEqual(Preco.objects.get(procedimento=proc, vigente_desde=self.hoje).valor, Decimal('165.00'))

    def test_criar_nasce_ativo_com_preco_de_hoje(self):
        resp = self.client.post(reverse('aranha:admin_criar_procedimento'),
                                {'nome': 'Limpeza Profunda', 'duracao_minutos': '50', 'preco': '120'})
        self.assertEqual(resp.status_code, 302)
        proc = Procedimento.objects.get(nome='Limpeza Profunda')
        self.assertTrue(proc.ativo)
        self.assertEqual(Preco.objects.get(procedimento=proc).vigente_desde, self.hoje)

    def test_acoes_so_post(self):
        proc = criar_procedimento()
        for url in (reverse('aranha:admin_criar_procedimento'),
                    reverse('aranha:admin_editar_procedimento', args=[proc.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)
