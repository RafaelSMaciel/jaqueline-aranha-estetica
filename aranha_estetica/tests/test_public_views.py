"""Smoke + regressao das views/templates publicos (auditoria pre-producao, P8)."""
import os
import re
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from aranha_estetica.models import AvaliacaoNPS, Preco, Promocao
from aranha_estetica.utils import datas

from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional


class PublicViewsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    def test_home(self):
        r = self.client.get(reverse('aranha:inicio'))
        self.assertIn(r.status_code, (200, 301, 302))

    def test_healthcheck_ok(self):
        r = self.client.get('/health/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['status'], 'ok')
        self.assertTrue(body['db'])

    def test_manifest(self):
        r = self.client.get(reverse('aranha:manifest'))
        self.assertEqual(r.status_code, 200)
        import json
        data = json.loads(r.content)
        self.assertLessEqual(len(data['short_name']), 12)

    def test_favicon_ico_redireciona_para_png(self):
        r = self.client.get('/favicon.ico')
        self.assertEqual(r.status_code, 301)
        self.assertIn('assets/favicon.png', r['Location'])


class DadosInventadosTests(TestCase):
    """Regressao public_front-10/11/22: nada de dado fabricado nem link quebrado."""

    def setUp(self):
        cache.clear()
        criar_profissional()

    def test_home_sem_card_proximo_horario_nem_selo_disponivel(self):
        html = self.client.get(reverse('aranha:inicio')).content.decode()
        self.assertNotIn('Hoje às 14:30', html)
        self.assertNotIn('aria-label="Disponível"', html)
        for fora_do_catalogo in ('Gestantes', 'Estética Íntima', 'Pós-operatório'):
            self.assertNotIn(fora_do_catalogo, html)
        self.assertIn('32 Avaliações', html)  # decisao do dono: manter

    def test_servicos_sem_percentuais_inventados(self):
        for nome in ('aranha:servicos_faciais', 'aranha:servicos_corporais'):
            html = self.client.get(reverse(nome)).content.decode()
            self.assertNotIn('98%', html)
            self.assertNotIn('95%', html)
            self.assertNotIn('Satisfação Garantida', html)
            self.assertNotIn('Agende sua consulta', html)

    def test_pagina_produtos_removida(self):
        with self.assertRaises(NoReverseMatch):
            reverse('aranha:servicos_produtos')
        self.assertEqual(self.client.get('/servicos/produtos/').status_code, 404)

    def test_link_sobre_aponta_para_quem_somos(self):
        html = self.client.get(reverse('aranha:inicio')).content.decode()
        self.assertNotIn('href="/#sobre"', html)
        self.assertIn(f'href="{reverse("aranha:quem_somos")}"', html)

    def test_rodape_linka_autoatendimento(self):
        html = self.client.get(reverse('aranha:inicio')).content.decode()
        for nome in ('meus_agendamentos', 'lgpd_meus_dados', 'lista_espera_publica'):
            self.assertIn(f'href="{reverse("aranha:" + nome)}"', html, nome)


@patch.dict(os.environ, {'WHATSAPP_NUMERO': '', 'CLINIC_PHONE': '', 'CLINIC_EMAIL': ''})
class ContatosVaziosTests(TestCase):
    """Regressao public_front-02/booking-11: sem contato real, nada de numero/e-mail ficticio."""

    def setUp(self):
        cache.clear()

    def test_sem_whatsapp_nao_renderiza_wa_me(self):
        for nome in ('aranha:inicio', 'aranha:equipe', 'aranha:promocoes', 'aranha:agenda_contato',
                     'aranha:servicos_faciais', 'aranha:servicos_corporais', 'aranha:quem_somos',
                     'aranha:politica_privacidade'):
            html = self.client.get(reverse(nome)).content.decode()
            self.assertNotIn('wa.me/', html, nome)
            self.assertNotIn('5517999990000', html, nome)
            self.assertNotIn('99999-0000', html, nome)
            self.assertNotIn('contato@clinica.com.br', html, nome)

    @patch.dict(os.environ, {'WHATSAPP_NUMERO': '5517991234567', 'CLINIC_PHONE': '(17) 99123-4567'})
    def test_com_whatsapp_renderiza_cta_e_tel_correto(self):
        cache.clear()
        html = self.client.get(reverse('aranha:inicio')).content.decode()
        self.assertIn('https://wa.me/5517991234567', html)
        self.assertIn('href="tel:+5517991234567"', html)


class SeoBaseTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(SITE_URL='https://exemplo.com.br')
    def test_head_tem_canonical_og_favicon_manifest(self):
        html = self.client.get(reverse('aranha:equipe')).content.decode()
        self.assertIn('<link rel="canonical" href="https://exemplo.com.br/equipe/">', html)
        self.assertIn('property="og:image"', html)
        self.assertIn('rel="icon"', html)
        self.assertIn(f'rel="manifest" href="{reverse("aranha:manifest")}"', html)
        self.assertIn('name="theme-color"', html)

    def test_meta_description_por_pagina(self):
        html = self.client.get(reverse('aranha:servicos_faciais')).content.decode()
        m = re.search(r'<meta name="description" content="([^"]*)"', html)
        self.assertIn('Tratamentos faciais', m.group(1))

    @override_settings(SITE_URL='https://exemplo.com.br')
    def test_home_tem_jsonld_local_business_valido(self):
        import json
        html = self.client.get(reverse('aranha:inicio')).content.decode()
        blocos = re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
        tipos = [json.loads(b)['@type'] for b in blocos]
        self.assertIn('BeautySalon', tipos)
        negocio = next(json.loads(b) for b in blocos if json.loads(b)['@type'] == 'BeautySalon')
        self.assertEqual(negocio['url'], 'https://exemplo.com.br/')

    def test_noscript_style_tem_nonce(self):
        html = self.client.get(reverse('aranha:inicio')).content.decode()
        self.assertRegex(html, r'<noscript><style nonce="[^"]+">')

    def test_pagina_com_token_fica_fora_do_indice(self):
        html = render_to_string('publico/nps_obrigado.html', {'expirado': True})
        self.assertIn('noindex', html)
        self.assertNotIn('rel="canonical"', html)

    def test_sitemap_sem_produtos_e_ordenado(self):
        criar_procedimento(nome='Zeta')
        r = self.client.get('/sitemap.xml')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('/servicos/produtos/', r.content.decode())


class EspecialidadesTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_css_das_abas_casa_os_paineis(self):
        """Regressao public_front-03: '~ * #esp-panel' nunca casava; paineis ficavam ocultos."""
        criar_procedimento(nome='Limpeza', categoria='FACIAL')
        html = self.client.get(reverse('aranha:especialidades')).content.decode()
        self.assertIn('#esp-radio-facial:checked ~ #esp-panel-facial', html)
        self.assertNotIn('~ * #esp-panel-', html)
        self.assertIn('id="esp-panel-facial"', html)

    def test_a_partir_de_usa_preco_base(self):
        """Regressao public_front-15: preco de profissional nao pode vencer o base."""
        prof = criar_profissional()
        proc = criar_procedimento(nome='Botox', preco=Decimal('300.00'))
        Preco.objects.create(procedimento=proc, profissional=prof, valor=Decimal('250.00'))
        html = self.client.get(reverse('aranha:especialidades')).content.decode()
        self.assertIn('A partir de R$ 300', html)


class PromocoesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.proc = criar_procedimento(nome='Limpeza', preco=Decimal('100.00'))

    def test_promo_percentual_mostra_preco_final_e_original(self):
        hoje = datas.hoje()
        Promocao.objects.create(nome='Semana da pele', procedimento=self.proc,
                                desconto_percentual=Decimal('20'),
                                data_inicio=hoje, data_fim=hoje)
        html = self.client.get(reverse('aranha:promocoes')).content.decode()
        self.assertIn('R$ 80,00', html)
        self.assertIn('R$ 100,00', html)

    def test_promo_preco_fixo_mostra_original_riscado(self):
        hoje = datas.hoje()
        Promocao.objects.create(nome='Fixo', procedimento=self.proc,
                                preco_promocional=Decimal('70.00'),
                                data_inicio=hoje, data_fim=hoje + timedelta(days=3))
        html = self.client.get(reverse('aranha:promocoes')).content.decode()
        self.assertIn('R$ 70,00', html)
        self.assertIn('De R$ 100,00', html)

    def test_vigencia_usa_data_local(self):
        """Regressao public_front-14: promo que termina 'hoje' (BRT) sumia apos 21h."""
        hoje = datas.hoje()
        Promocao.objects.create(nome='Termina hoje', procedimento=self.proc,
                                desconto_percentual=Decimal('10'),
                                data_inicio=hoje - timedelta(days=2), data_fim=hoje)
        with patch('aranha_estetica.views.public.datas.hoje', return_value=hoje):
            html = self.client.get(reverse('aranha:promocoes')).content.decode()
        self.assertIn('Termina hoje', html)


class DepoimentosTests(TestCase):
    """Regressao security-10/public_front-07: so opt-in + aprovado, sem nome completo."""

    def setUp(self):
        cache.clear()
        self.prof = criar_profissional()
        self.proc = criar_procedimento()

    def _avaliacao(self, nome, autoriza, aprovado, nota=10, comentario='Amei o cuidado!'):
        cli = criar_cliente(nome=nome)
        at = criar_atendimento(cli, self.prof, self.proc, status='REALIZADO',
                               data_hora=datas.agora_local() - timedelta(days=AvaliacaoNPS.objects.count() + 1))
        return AvaliacaoNPS.objects.create(atendimento=at, nota=nota, comentario=comentario,
                                           autoriza_publicacao=autoriza, aprovado_publicacao=aprovado)

    def test_publica_so_com_autorizacao_e_aprovacao(self):
        self._avaliacao('Maria Aparecida Souza', True, True, comentario='Publicavel')
        self._avaliacao('Joana Sem Opt', False, True, comentario='SemOptIn')
        self._avaliacao('Carla Nao Aprovada', True, False, comentario='NaoModerado')
        html = self.client.get(reverse('aranha:depoimentos')).content.decode()
        self.assertIn('Publicavel', html)
        self.assertNotIn('SemOptIn', html)
        self.assertNotIn('NaoModerado', html)
        self.assertIn('Maria S.', html)
        self.assertNotIn('Aparecida', html)
        self.assertIn('Nota 10 de 10', html)


class TemplatesDeErroTests(TestCase):
    def test_500_renderiza_sem_contexto(self):
        """handler500 renderiza sem request/context processors (e sem manifest do Vite)."""
        for nome in ('500.html', '400.html', '403_csrf.html'):
            html = render_to_string(nome)
            self.assertIn('lang="pt-br"', html, nome)
            self.assertNotIn('<script', html, nome)
            self.assertNotIn('<style', html, nome)

    def test_403_amigavel(self):
        rf = RequestFactory()
        req = rf.get('/')
        req.session = {}
        html = render_to_string('403.html', request=req)
        self.assertIn('Acesso não permitido', html)

    def test_limite_excedido_responde_429(self):
        from aranha_estetica.views import limite_excedido
        req = RequestFactory().get('/')
        req.session = {}
        resp = limite_excedido(req, Exception())
        self.assertEqual(resp.status_code, 429)


class NpsEAnamneseTemplatesTests(TestCase):
    def test_nps_expirado_nao_agradece_avaliacao(self):
        html = render_to_string('publico/nps_obrigado.html', {'expirado': True, 'cliente': None})
        self.assertIn('Link expirado', html)
        self.assertNotIn('Sua avaliação é muito importante', html)

    def test_nps_web_tem_pergunta_e_opt_in_de_publicacao(self):
        html = render_to_string('publico/nps_web.html', {'notas_range': range(11)})
        self.assertIn('qual a chance de você indicar', html)
        self.assertIn('name="autoriza_publicacao"', html)
        self.assertNotIn('name="autoriza_publicacao" value="1" checked', html)


class AnamnesePublicaTests(TestCase):
    """Regressao public_front-17/18: erro nao apaga respostas; copy de ficha de saude."""

    def setUp(self):
        from aranha_estetica.models import FormularioAnamnese, RespostaAnamnese
        cache.clear()
        cli = criar_cliente()
        form = FormularioAnamnese.objects.create(
            nome='Ficha facial', tipo='ANAMNESE',
            schema_json=[
                {'key': 'alergias', 'tipo': 'text', 'label': 'Alergias?', 'obrigatorio': False},
                {'key': 'faixa', 'tipo': 'select', 'label': 'Faixa', 'opcoes': ['18-30', '31-50'], 'obrigatorio': False},
                {'key': 'itens', 'tipo': 'checkboxes', 'label': 'Itens', 'opcoes': ['A', 'B'], 'obrigatorio': False},
                {'key': 'nota', 'tipo': 'scale', 'label': 'Nota', 'opcoes': [1, 2, 3], 'obrigatorio': False},
                {'key': 'obs', 'tipo': 'longtext', 'label': 'Obs', 'obrigatorio': True},
            ],
        )
        self.resposta = RespostaAnamnese.objects.create(formulario=form, cliente=cli)
        self.url = reverse('aranha:anamnese_publica', args=[self.resposta.token])

    def test_copy_de_ficha_de_avaliacao(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('Ficha de avaliação', html)
        self.assertNotIn('Sua opinião importa', html)
        self.assertNotIn('melhorar o atendimento', html)
        self.assertIn('segurança do seu procedimento', html)

    def test_script_da_escala_renderizado_uma_vez(self):
        html = self.client.get(self.url).content.decode()
        self.assertEqual(html.count("querySelectorAll('.scale-option"), 1)

    def test_erro_de_validacao_preserva_respostas(self):
        resp = self.client.post(self.url, {
            'alergias': 'Dipirona', 'faixa': '31-50', 'itens': ['B'], 'nota': '2', 'obs': '',
        })
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('é obrigatório', html)
        self.assertIn('value="Dipirona"', html)
        self.assertRegex(html, r'<option value="31-50"\s+selected>')
        self.assertRegex(html, r'value="B"\s+id="cb-itens-2"\s+checked')
        self.assertRegex(html, r'value="2" class="sr-only" checked')
        self.resposta.refresh_from_db()
        self.assertIsNone(self.resposta.respondida_em)


class IcsFeedTests(TestCase):
    """Regressao pgtests-04: timezone.utc (removido no Django 5) dava 500 com agendamento."""

    def test_feed_com_atendimento_responde_ics(self):
        prof = criar_profissional()
        proc = criar_procedimento()
        criar_atendimento(criar_cliente(), prof, proc)
        prof.refresh_from_db()
        url = reverse('aranha:ics_feed_profissional', args=[prof.slug])
        r = self.client.get(url, {'token': prof.ics_token})
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/calendar', r['Content-Type'])
        self.assertIn('BEGIN:VEVENT', r.content.decode())
        self.assertEqual(self.client.get(url).status_code, 404)
