"""Achado 166: envio recusado nos modais do painel (procedimento, promocao, pacote).

Antes: messages.error + redirect -> o <dialog> fechava, o digitado se perdia, o aviso nao dizia
qual campo e o toast de erro sumia em 5 s. Agora a lista volta (200) com o modal marcado p/
reabrir (data-erro), o que foi digitado e o erro no campo (aria-invalid + aria-describedby).
"""
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.forms.cadastros import ProcedimentoForm
from aranha_estetica.forms.painel import TermoForm, avisar_erros
from aranha_estetica.models import Pacote, Procedimento, Promocao, Usuario

from .factories import criar_pacote, criar_procedimento, criar_profissional


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class _Admin(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email='admin-modal@test.com', password='senha-forte-123', nome='Admin Modal')
        self.client.force_login(self.admin)

    def assertCampoRecusado(self, html, campo_id, texto):
        """O campo sai com aria-invalid apontando p/ a mensagem, e a mensagem esta na tela."""
        tag = re.search(rf'<(?:input|select|textarea)[^>]*\bid="{re.escape(campo_id)}"[^>]*>', html)
        self.assertIsNotNone(tag, f'campo {campo_id} nao renderizado')
        self.assertIn('aria-invalid="true"', tag.group(0))
        self.assertIn(f'aria-describedby="{campo_id}-erro"', tag.group(0))
        self.assertRegex(html, rf'id="{re.escape(campo_id)}-erro"[^>]*>\s*{re.escape(texto)}')


class AvisarErrosTests(TestCase):
    def test_prefixa_o_rotulo_do_campo(self):
        request = self.client.get('/').wsgi_request
        form = ProcedimentoForm({'nome': 'X', 'preco': '1e30'})
        self.assertFalse(form.is_valid())
        avisar_erros(request, form)
        self.assertEqual([str(m) for m in get_messages(request)], ['Preço (R$): Duração ou preço inválido.'])

    def test_erro_geral_e_mensagem_que_ja_cita_o_campo_ficam_como_estao(self):
        request = self.client.get('/').wsgi_request
        avisar_erros(request, TermoForm({'titulo': 'a'}))   # clean(): erro geral
        avisar_erros(request, TermoForm({'titulo': 'a', 'conteudo': 'b', 'versao': 'v' * 21}))
        self.assertEqual([str(m) for m in get_messages(request)],
                         ['Informe título e conteúdo do termo.', 'Versão deve ter no máximo 20 caracteres.'])


class ToastDeErroTests(TestCase):
    def test_toast_de_erro_nao_some_sozinho(self):
        base = (Path(settings.BASE_DIR) / 'aranha_estetica' / 'templates' / 'painel' / 'base_v2.html')
        html = base.read_text(encoding='utf-8')
        self.assertNotIn('setTimeout', html)
        self.assertIn('alertaDismiss', html)   # quem le devagar fecha quando quiser


class ProcedimentoModalTests(_Admin):
    def test_novo_recusado_volta_com_o_digitado_e_o_erro_no_campo(self):
        prof = criar_profissional('Ana')
        resp = self.client.post(reverse('aranha:admin_criar_procedimento'), {
            'nome': 'Peeling Glow', 'descricao': 'renova', 'duracao_minutos': '45',
            'categoria': 'CORPORAL', 'preco': '1e30', 'profissionais': [str(prof.pk)]})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertFalse(Procedimento.objects.filter(nome='Peeling Glow').exists())
        self.assertIn('value="Peeling Glow"', html)
        self.assertIn('value="45"', html)
        self.assertIn('>renova</textarea>', html)
        self.assertRegex(html, r'<option value="CORPORAL" selected>')
        self.assertRegex(html, rf'value="{prof.pk}" checked')
        self.assertCampoRecusado(html, 'novo_preco', 'Duração ou preço inválido.')
        self.assertIn('Preço (R$): Duração ou preço inválido.', _mensagens(resp))

    def test_edicao_recusada_reabre_o_modal_do_item(self):
        proc = criar_procedimento('Drenagem')
        resp = self.client.post(reverse('aranha:admin_editar_procedimento', args=[proc.pk]), {
            'nome': 'Drenagem Nova', 'duracao_minutos': '2', 'categoria': 'FACIAL', 'ativo': '1'})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        proc.refresh_from_db()
        self.assertEqual(proc.nome, 'Drenagem')   # nada salvo
        self.assertEqual(html.count('data-erro="1"'), 1)
        self.assertIn('value="Drenagem Nova"', html)
        self.assertCampoRecusado(html, f'proc-{proc.pk}-duracao', 'Duração ou preço inválido.')

    def test_edicao_na_segunda_pagina_volta_na_pagina_do_item(self):
        for i in range(31):
            Procedimento.objects.create(nome=f'Proc {i:02d}', duracao_minutos=30, categoria='FACIAL', ativo=True)
        ultimo = Procedimento.objects.get(nome='Proc 30')
        resp = self.client.post(reverse('aranha:admin_editar_procedimento', args=[ultimo.pk]),
                                {'nome': '', 'ativo': '1'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['procedimentos'].number, 2)
        self.assertContains(resp, 'data-erro="1"', count=1)

    def test_valido_continua_com_redirect(self):
        resp = self.client.post(reverse('aranha:admin_criar_procedimento'),
                                {'nome': 'Limpeza', 'duracao_minutos': '30'})
        self.assertEqual(resp.status_code, 302)


class PromocaoModalTests(_Admin):
    def setUp(self):
        super().setUp()
        self.proc = criar_procedimento('Limpeza')
        self.hoje = timezone.localdate()

    def test_nova_recusada_reabre_o_modal_com_o_digitado(self):
        resp = self.client.post(reverse('aranha:admin_criar_promocao'), {
            'nome': 'Semana Zen', 'descricao': 'relaxe', 'procedimento': '', 'preco_promocional': '50',
            'data_inicio': self.hoje.isoformat(), 'data_fim': (self.hoje + timedelta(days=5)).isoformat(),
            'ativa': '1'})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertFalse(Promocao.objects.filter(nome='Semana Zen').exists())
        self.assertIn('data-erro="1"', html)
        self.assertIn('value="Semana Zen"', html)
        self.assertIn('>relaxe</textarea>', html)
        self.assertCampoRecusado(html, 'promo-nova-preco', 'Promoção geral')
        self.assertIn('data-resumo-erro', html)   # o aviso geral tambem dentro do modal (resto inerte)

    def test_edicao_com_fim_antes_do_inicio_marca_a_data_de_fim(self):
        promo = Promocao.objects.create(nome='Outono', desconto_percentual=Decimal('10'), procedimento=self.proc,
                                        data_inicio=self.hoje, data_fim=self.hoje + timedelta(days=10))
        resp = self.client.post(reverse('aranha:admin_editar_promocao', args=[promo.pk]), {
            'nome': 'Outono Novo', 'procedimento': str(self.proc.pk), 'desconto': '12',
            'data_inicio': self.hoje.isoformat(), 'data_fim': (self.hoje - timedelta(days=1)).isoformat(),
            'ativa': '1'})
        self.assertEqual(resp.status_code, 200)
        promo.refresh_from_db()
        self.assertEqual(promo.nome, 'Outono')
        html = resp.content.decode()
        self.assertEqual(html.count('data-erro="1"'), 1)
        self.assertIn('value="Outono Novo"', html)
        self.assertCampoRecusado(html, f'promo-{promo.pk}-fim', 'A data de fim deve ser igual ou posterior')
        self.assertTrue(any('Erro ao atualizar promoção' in m for m in _mensagens(resp)))


class PacoteModalTests(_Admin):
    def setUp(self):
        super().setUp()
        self.proc = criar_procedimento('Drenagem')

    def test_novo_recusado_mantem_itens_e_marca_o_preco(self):
        resp = self.client.post(reverse('aranha:admin_criar_pacote'), {
            'nome': 'Pacote Leveza', 'preco_total': 'abc', 'validade_meses': '6',
            'procedimento_ids': [str(self.proc.pk)], 'quantidades': ['4']})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertFalse(Pacote.objects.filter(nome='Pacote Leveza').exists())
        self.assertIn('value="Pacote Leveza"', html)
        self.assertRegex(html, rf'<option value="{self.proc.pk}" selected>Drenagem</option>')
        self.assertIn('name="quantidades" value="4"', html)
        self.assertCampoRecusado(html, 'pacote-novo-preco', 'Preço inválido')
        self.assertIn('Preço inválido: use números, ex.: 1350,00.', _mensagens(resp))

    def test_edicao_sem_nome_reabre_o_modal_do_pacote(self):
        pacote = criar_pacote('Pacote Glow', procedimento=self.proc)
        resp = self.client.post(reverse('aranha:admin_editar_pacote', args=[pacote.pk]), {
            'nome': '  ', 'preco_total': '700', 'validade_meses': '6',
            'procedimento_ids': [str(self.proc.pk)], 'quantidades': ['3']})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertEqual(html.count('data-erro="1"'), 1)
        self.assertCampoRecusado(html, f'pacote-{pacote.pk}-nome', 'Nome do pacote é obrigatório.')
        self.assertIn('value="700"', html)
        pacote.refresh_from_db()
        self.assertEqual(pacote.nome, 'Pacote Glow')
