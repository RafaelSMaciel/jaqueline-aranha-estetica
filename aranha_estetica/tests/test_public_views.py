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


class FaqHonestoTests(TestCase):
    """Regressao gap3-01/gap4-03: FAQ (tambem vai p/ o JSON-LD) so promete o que existe."""

    def setUp(self):
        cache.clear()

    def _respostas(self):
        resp = self.client.get(reverse('aranha:inicio'))
        return ' '.join(i['a'] for c in resp.context['faq_categorias'] for i in c['itens']), resp

    def test_sem_credito_por_indicacao(self):
        respostas, resp = self._respostas()
        html = resp.content.decode()
        self.assertNotIn('crédito na sua carteira', html)
        self.assertNotIn('benefício por indicação', html)
        self.assertNotIn('indicar uma amiga', respostas)

    @patch.dict(os.environ, {'WHATSAPP_NUMERO': '', 'CLINIC_PHONE': '', 'CLINIC_EMAIL': ''})
    def test_sem_canais_faq_nao_cita_whatsapp_telefone_nem_email(self):
        respostas, _ = self._respostas()
        self.assertNotIn('WhatsApp', respostas)
        self.assertNotIn('por telefone', respostas)
        self.assertNotIn('e-mail', respostas)
        self.assertIn('presencialmente na clínica', respostas)
        self.assertIn('agendar online pelo nosso site', respostas)

    @patch.dict(os.environ, {'WHATSAPP_NUMERO': '5517991234567', 'CLINIC_PHONE': '',
                             'CLINIC_EMAIL': 'oi@clinica.com.br'})
    def test_com_canais_faq_cita_so_os_existentes(self):
        respostas, _ = self._respostas()
        self.assertIn('ou pelo WhatsApp.', respostas)
        self.assertIn('pelo WhatsApp, por e-mail', respostas)
        self.assertNotIn('por telefone', respostas)


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


class PrecoVitrineComPromocaoTests(TestCase):
    """Contrato 2: 'A partir de' = valor que o agendamento gravaria hoje (preco_com_promocao)."""

    def setUp(self):
        cache.clear()
        self.proc = criar_procedimento(nome='Drenagem', categoria='CORPORAL', preco=Decimal('100.00'))
        hoje = datas.hoje()
        Promocao.objects.create(nome='Semana do corpo', procedimento=self.proc,
                                desconto_percentual=Decimal('15'),
                                data_inicio=hoje, data_fim=hoje + timedelta(days=2))

    def test_especialidades_mostra_valor_com_promo_e_cheio_riscado(self):
        from aranha_estetica.utils.precos import preco_com_promocao
        final, _promo, _cheio = preco_com_promocao(self.proc)
        html = self.client.get(reverse('aranha:especialidades')).content.decode()
        self.assertEqual(final, Decimal('85.00'))
        self.assertIn('A partir de R$ 85', html)
        self.assertRegex(html, r'<s><span class="sr-only">Preço sem promoção: </span>R\$ 100')
        self.assertIn('Promoção: Semana do corpo', html)

    def test_servico_detalhe_usa_mesmo_valor(self):
        self.proc.refresh_from_db()
        html = self.client.get(
            reverse('aranha:servico_detalhe', args=[self.proc.slug])
        ).content.decode()
        self.assertIn('A partir de R$ 85', html)
        self.assertIn('"price": "85.00"', html)
        self.assertIn('Semana do corpo', html)

    def test_sem_promocao_nao_risca_preco(self):
        Promocao.objects.all().delete()
        html = self.client.get(reverse('aranha:especialidades')).content.decode()
        self.assertIn('A partir de R$ 100', html)
        self.assertNotIn('Preço sem promoção', html)


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

    def test_validade_se_refere_a_data_do_atendimento(self):
        hoje = datas.hoje()
        Promocao.objects.create(nome='Datas', procedimento=self.proc,
                                desconto_percentual=Decimal('10'),
                                data_inicio=hoje, data_fim=hoje)
        html = self.client.get(reverse('aranha:promocoes')).content.decode()
        self.assertIn('Válido para atendimentos de', html)
        self.assertIn('R$ 90,00', html)

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


class AnamneseBoolTests(TestCase):
    """Regressao gap2-09: bool obrigatorio ('Está gestante?') so aceitava 'Sim'."""

    def setUp(self):
        from aranha_estetica.models import FormularioAnamnese, RespostaAnamnese
        cache.clear()
        form = FormularioAnamnese.objects.create(
            nome='Ficha corporal', tipo='ANAMNESE',
            schema_json=[
                {'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante?', 'obrigatorio': True},
                {'key': 'fuma', 'tipo': 'bool', 'label': 'Fuma?', 'obrigatorio': False},
            ],
        )
        self.resposta = RespostaAnamnese.objects.create(formulario=form, cliente=criar_cliente())
        self.url = reverse('aranha:anamnese_publica', args=[self.resposta.token])

    def test_renderiza_radios_sim_e_nao(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('<legend', html)
        self.assertIn('name="gestante" value="sim"', html)
        self.assertIn('name="gestante" value="nao"', html)
        self.assertNotIn('type="checkbox" name="gestante"', html)

    def test_nao_em_bool_obrigatorio_grava_false(self):
        resp = self.client.post(self.url, {'gestante': 'nao', 'consent_dados_saude': 'on'})
        self.assertRedirects(resp, reverse('aranha:anamnese_obrigado'), fetch_redirect_response=False)
        self.resposta.refresh_from_db()
        self.assertIs(self.resposta.respostas_json['gestante'], False)
        self.assertNotIn('fuma', self.resposta.respostas_json)  # sem resposta != 'Não'

    def test_sim_grava_true(self):
        self.client.post(self.url, {'gestante': 'sim', 'fuma': 'nao', 'consent_dados_saude': 'on'})
        self.resposta.refresh_from_db()
        self.assertIs(self.resposta.respostas_json['gestante'], True)
        self.assertIs(self.resposta.respostas_json['fuma'], False)

    def test_sem_valor_em_obrigatorio_devolve_erro_e_mantem_escolha(self):
        resp = self.client.post(self.url, {'fuma': 'sim'})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('é obrigatório', html)
        self.assertRegex(html, r'name="fuma" value="sim"\s+checked')
        self.resposta.refresh_from_db()
        self.assertIsNone(self.resposta.respondida_em)

    def test_ficha_sem_consentimento_art11_nao_grava(self):
        resp = self.client.post(self.url, {'gestante': 'nao'})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('autorização de uso das suas informações de saúde', html)
        self.assertIn('name="consent_dados_saude"', html)
        self.resposta.refresh_from_db()
        self.assertIsNone(self.resposta.respondida_em)

    def test_ficha_com_consentimento_registra_trilha(self):
        from aranha_estetica.models import LogAuditoria
        self.client.post(self.url, {'gestante': 'nao', 'consent_dados_saude': 'on'})
        log = LogAuditoria.objects.filter(
            tabela='resposta_anamnese', registro_id=self.resposta.pk, acao__icontains='art. 11',
        ).first()
        self.assertIsNotNone(log)
        self.assertIn('LGPD, art. 11', log.detalhes['texto'])

    @override_settings(CLIENT_IP_HEADER='')
    def test_ficha_com_consentimento_grava_aceite_saude_com_prova(self):
        """Contrato 1: o consentimento da ficha publica e AceiteTermo da versao SAUDE."""
        from aranha_estetica.models import AceiteTermo, LogAuditoria, VersaoTermo
        prof = criar_profissional()
        atd = criar_atendimento(self.resposta.cliente, prof, criar_procedimento(profissional=prof))
        self.resposta.atendimento = atd
        self.resposta.save(update_fields=['atendimento'])
        saude = VersaoTermo.saude_vigente()
        self.assertIn(saude.conteudo, self.client.get(self.url).content.decode())

        self.client.post(self.url, {'gestante': 'nao', 'consent_dados_saude': 'on'},
                         REMOTE_ADDR='200.10.20.30', HTTP_USER_AGENT='Tablet Recepcao/1.0')
        aceite = AceiteTermo.objects.get(cliente=self.resposta.cliente, versao_termo=saude)
        self.assertEqual(aceite.atendimento, atd)
        self.assertEqual((aceite.ip, aceite.user_agent), ('200.10.20.30', 'Tablet Recepcao/1.0'))
        self.assertEqual(aceite.conteudo_sha256, saude.sha256_conteudo)
        log = LogAuditoria.objects.get(tabela='resposta_anamnese', acao__icontains='art. 11')
        self.assertEqual(log.detalhes['aceite_id'], aceite.pk)

    def test_ficha_sem_consentimento_nao_grava_aceite(self):
        from aranha_estetica.models import AceiteTermo
        self.client.post(self.url, {'gestante': 'nao'})
        self.assertFalse(AceiteTermo.objects.exists())

    def test_valor_desconhecido_e_recusado(self):
        resp = self.client.post(self.url, {'gestante': 'talvez'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('responda sim ou não', resp.content.decode().lower())
        self.resposta.refresh_from_db()
        self.assertIsNone(self.resposta.respondida_em)


class TermoTemplatesTests(TestCase):
    """Regressao gap1-10: codigo cru do tipo, texto sem foco por teclado, 'assinado' sem gravar."""

    def setUp(self):
        from aranha_estetica.models import VersaoTermo
        cache.clear()
        self.cli = criar_cliente(nome='Ana Paula Souza')
        proc = criar_procedimento(nome='Peeling')
        self.at = criar_atendimento(self.cli, criar_profissional(), proc)
        self.termo = VersaoTermo(pk=987, tipo='PROCEDIMENTO', procedimento=proc,
                                 titulo='Termo do peeling', conteudo='Texto do termo.',
                                 versao='2.0', vigente_desde=datas.hoje())

    def test_assinatura_mostra_tipo_legivel_data_e_regiao_focavel(self):
        html = render_to_string('publico/termo_assinatura.html', {
            'cliente': self.cli, 'atendimento': self.at, 'termos_pendentes': [self.termo],
        })
        self.assertIn('Termo de Procedimento', html)
        self.assertNotIn('>PROCEDIMENTO<', html)
        self.assertIn('tabindex="0" role="region" aria-label="Texto do termo Termo do peeling"', html)
        self.assertIn('Confirmar aceite', html)
        self.assertIn('versão 2.0', html)
        self.assertIn(datas.fmt_local(self.at.data_hora_inicio, '%d/%m/%Y'), html)
        self.assertIn(datas.fmt_local(self.at.data_hora_inicio, '%H:%M'), html)
        self.assertNotIn('#dbeafe', html)

    def test_obrigado_indisponivel_nao_diz_que_registrou(self):
        html = render_to_string('publico/termo_obrigado.html', {'cliente': self.cli, 'indisponivel': True})
        self.assertNotIn('registrado', html)
        self.assertIn('não está mais ativo', html)

    def test_obrigado_expirado(self):
        html = render_to_string('publico/termo_obrigado.html', {'cliente': self.cli, 'expirado': True})
        self.assertIn('Link expirado', html)
        self.assertNotIn('registrado', html)

    def test_obrigado_sucesso(self):
        html = render_to_string('publico/termo_obrigado.html', {'cliente': self.cli})
        self.assertIn('Aceite registrado', html)
        self.assertIn('Obrigado, Ana.', html)


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


class PromocoesCoerentesComAgendamentoTests(TestCase):
    """Regressao followups-promocoes-vitrine-diverge-agendamento (FU #2/#18):
    /promocoes/ so anuncia o preco que utils.precos.preco_com_promocao grava."""

    def setUp(self):
        cache.clear()
        self.hoje = datas.hoje()

    def _promo(self, **kw):
        base = dict(data_inicio=self.hoje, data_fim=self.hoje + timedelta(days=3))
        base.update(kw)
        return Promocao.objects.create(**base)

    def test_percentual_sobre_preco_so_do_profissional_bate_com_agendamento(self):
        from aranha_estetica.utils.precos import preco_com_promocao
        prof = criar_profissional()
        proc = criar_procedimento(nome='Bioestimulador', preco=None, profissional=prof)
        Preco.objects.create(procedimento=proc, profissional=prof, valor=Decimal('1000.00'))
        self._promo(nome='Mes do colageno', procedimento=proc, desconto_percentual=Decimal('20'))

        final, promo, cheio = preco_com_promocao(proc, prof)
        html = self.client.get(reverse('aranha:promocoes')).content.decode()
        self.assertEqual((final, cheio), (Decimal('800.00'), Decimal('1000.00')))
        self.assertIsNotNone(promo)
        self.assertIn('R$ 800,00', html)
        self.assertIn('De R$ 1.000,00', html)
        vitrine = self.client.get(reverse('aranha:especialidades')).content.decode()
        self.assertIn('A partir de R$ 800,00', vitrine)

    def test_promo_geral_de_preco_fixo_nem_entra_no_banco(self):
        """O booking ignora promo geral de preco fixo: o banco a recusa (CHECK 0047),
        entao a vitrine nunca oferece 'R$ 49' p/ o catalogo inteiro."""
        from django.db import IntegrityError, transaction
        criar_procedimento(nome='Limpeza', preco=Decimal('200.00'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._promo(nome='Tudo por 49', preco_promocional=Decimal('49.00'))
        html = self.client.get(reverse('aranha:promocoes')).content.decode()
        self.assertNotIn('Tudo por 49', html)
        self.assertNotIn('R$ 49', html)

    def test_promo_geral_percentual_aparece_sem_preco_unico(self):
        criar_procedimento(nome='Limpeza', preco=Decimal('200.00'))
        self._promo(nome='Semana da cliente', desconto_percentual=Decimal('10'))
        html = self.client.get(reverse('aranha:promocoes')).content.decode()
        self.assertIn('Semana da cliente', html)
        self.assertIn('-10%', html)
        self.assertNotIn('R$ 180', html)

    def test_promo_superada_por_outra_nao_mostra_preco_que_nao_sera_gravado(self):
        from aranha_estetica.utils.precos import preco_com_promocao
        proc = criar_procedimento(nome='Peeling', preco=Decimal('100.00'))
        self._promo(nome='Dez off', procedimento=proc, desconto_percentual=Decimal('10'))
        self._promo(nome='Trinta off', procedimento=proc, desconto_percentual=Decimal('30'))
        html = self.client.get(reverse('aranha:promocoes')).content.decode()
        self.assertEqual(preco_com_promocao(proc)[0], Decimal('70.00'))
        self.assertIn('R$ 70,00', html)
        self.assertNotIn('R$ 90,00', html)

    def test_percentual_sem_preco_nenhum_nao_e_anunciado(self):
        proc = criar_procedimento(nome='Sem preco', preco=None)
        self._promo(nome='Promo fantasma', procedimento=proc, desconto_percentual=Decimal('20'))
        html = self.client.get(reverse('aranha:promocoes')).content.decode()
        self.assertNotIn('Promo fantasma', html)


class PrecoFormatoBrlTests(TestCase):
    """Regressao rev_booking-11: vitrine mostrava 'R$ 1200,00' e o wizard 'R$ 1.200,00'."""

    def setUp(self):
        cache.clear()
        self.proc = criar_procedimento(nome='Bioestimulador', preco=Decimal('1200.00'))

    def test_especialidades_usa_separador_de_milhar(self):
        html = self.client.get(reverse('aranha:especialidades')).content.decode()
        self.assertIn('R$ 1.200,00', html)
        self.assertNotIn('R$ 1200,00', html)

    def test_servico_detalhe_usa_separador_de_milhar(self):
        self.proc.refresh_from_db()
        html = self.client.get(reverse('aranha:servico_detalhe', args=[self.proc.slug])).content.decode()
        self.assertIn('R$ 1.200,00', html)
        self.assertNotIn('R$ 1200,00', html)
        self.assertIn('"price": "1200.00"', html)  # JSON-LD continua com ponto decimal

    def test_promocoes_usa_separador_de_milhar(self):
        hoje = datas.hoje()
        Promocao.objects.create(nome='Colageno', procedimento=self.proc, desconto_percentual=Decimal('10'),
                                data_inicio=hoje, data_fim=hoje)
        html = self.client.get(reverse('aranha:promocoes')).content.decode()
        self.assertIn('De R$ 1.200,00', html)
        self.assertIn('R$ 1.080,00', html)


class CopySemPromessaTests(TestCase):
    """Regressao rev_booking-03: copy de servicos prometia resultado/seguranca absoluta
    (CDC art. 37), contra os Termos de Uso ('nao ha garantia de resultado')."""

    PROMESSAS = re.compile(r'garant|comprovad|definitiv|toxinas|indolor|todos os fototipos', re.IGNORECASE)

    def test_paginas_de_servico_sem_promessa_de_resultado(self):
        cache.clear()
        for nome in ('aranha:servicos_faciais', 'aranha:servicos_corporais', 'aranha:especialidades'):
            html = self.client.get(reverse(nome)).content.decode()
            self.assertEqual(self.PROMESSAS.findall(html), [], nome)


class DepoimentoAposEsquecimentoTests(TestCase):
    """Regressao rev_security-02 (defesa em profundidade): titular anonimizado ou
    excluido nao continua com depoimento no ar nem aparece como '[ANONIMIZADO-n]'."""

    def setUp(self):
        cache.clear()
        self.prof = criar_profissional()
        self.proc = criar_procedimento()

    def _avaliacao(self, nome, comentario):
        cli = criar_cliente(nome=nome)
        at = criar_atendimento(cli, self.prof, self.proc, status='REALIZADO',
                               data_hora=datas.agora_local() - timedelta(days=AvaliacaoNPS.objects.count() + 1))
        AvaliacaoNPS.objects.create(atendimento=at, nota=10, comentario=comentario,
                                    autoriza_publicacao=True, aprovado_publicacao=True)
        return cli

    def test_depoimento_de_titular_anonimizado_sai_do_ar(self):
        from aranha_estetica.models import Cliente
        cli = self._avaliacao('Paula Andrade', 'Comentario da titular')
        self._avaliacao('Rita Lopes', 'Comentario que fica')
        Cliente.all_objects.filter(pk=cli.pk).update(nome=f'[ANONIMIZADO-{cli.pk}]')
        html = self.client.get(reverse('aranha:depoimentos')).content.decode()
        self.assertNotIn('Comentario da titular', html)
        self.assertNotIn('[ANONIMIZADO-', html)
        self.assertIn('Comentario que fica', html)

    def test_depoimento_de_cliente_excluido_sai_do_ar(self):
        cli = self._avaliacao('Paula Andrade', 'Comentario apagado')
        cli.soft_delete()
        html = self.client.get(reverse('aranha:depoimentos')).content.decode()
        self.assertNotIn('Comentario apagado', html)


class AnamnesePublicaSemCacheTests(TestCase):
    """Ficha de saude (LGPD art. 11) aberta num tablet/PC compartilhado nao pode
    voltar pelo cache/bfcache (followups-never-cache-paginas-privadas)."""

    def test_anamnese_e_pesquisa_respondem_no_store(self):
        from aranha_estetica.models import FormularioAnamnese, RespostaAnamnese
        cli = criar_cliente()
        for tipo, rota in (('ANAMNESE', 'aranha:anamnese_publica'), ('PESQUISA', 'aranha:pesquisa_publica')):
            form = FormularioAnamnese.objects.create(
                nome=f'Ficha {tipo}', tipo=tipo,
                schema_json=[{'key': 'alergias', 'tipo': 'text', 'label': 'Alergias?', 'obrigatorio': True}],
            )
            resposta = RespostaAnamnese.objects.create(formulario=form, cliente=cli)
            url = reverse(rota, args=[resposta.token])
            for resp in (self.client.get(url), self.client.post(url, {'alergias': ''})):
                self.assertEqual(resp.status_code, 200, rota)
                self.assertIn('no-store', resp['Cache-Control'], rota)


class CtaAgendarSemSmsTests(TestCase):
    """Regressao rev_booking-02 (parte do site): sem provedor de SMS o wizard nao
    conclui (OTP obrigatorio) — CTAs globais 'Agendar' levam direto ao WhatsApp."""

    def _html(self):
        cache.clear()
        return self.client.get(reverse('aranha:quem_somos')).content.decode()

    @patch.dict(os.environ, {'WHATSAPP_NUMERO': '5517991234567'})
    def test_sem_sms_cta_do_cabecalho_e_rodape_vai_ao_whatsapp(self):
        with patch('aranha_estetica.utils.sms.sms_disponivel', return_value=False):
            html = self._html()
        self.assertIn('aria-label="Agendar pelo WhatsApp"', html)
        self.assertIn('Agendar pelo WhatsApp</a>', html)  # menu mobile
        self.assertIn('https://wa.me/5517991234567?text=Ol%C3%A1%21%20Gostaria%20de%20agendar', html)
        self.assertIn('Reserve seu horário pelo WhatsApp.', html)

    @patch.dict(os.environ, {'WHATSAPP_NUMERO': '5517991234567'})
    def test_com_sms_cta_segue_para_o_wizard(self):
        with patch('aranha_estetica.utils.sms.sms_disponivel', return_value=True):
            html = self._html()
        self.assertNotIn('Agendar pelo WhatsApp', html)
        self.assertIn('href="%s"' % reverse('aranha:agendamento_publico'), html)

    @patch.dict(os.environ, {'WHATSAPP_NUMERO': ''})
    def test_sem_sms_e_sem_whatsapp_mantem_o_wizard(self):
        with patch('aranha_estetica.utils.sms.sms_disponivel', return_value=False):
            html = self._html()
        self.assertNotIn('wa.me/', html)
        self.assertIn('href="%s"' % reverse('aranha:agendamento_publico'), html)
