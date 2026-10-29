"""Politica de privacidade como fonte unica (decisao 47), DSAR sem saude no self-service (decisao 38)
e o check aranha.W016 (prazo do settings x prazo publicado no termo vigente)."""
import json
import time
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica import checks as aranha_checks
from aranha_estetica import inventario_lgpd as inv
from aranha_estetica.conteudo import lgpd as conteudo
from aranha_estetica.models import CodigoOtp, Prontuario, VersaoTermo
from aranha_estetica.services import lgpd

from .factories import criar_cliente


class PoliticaFonteUnicaTests(TestCase):
    def test_v1_vigente_e_o_texto_completo(self):
        termo = VersaoTermo.lgpd_vigente()
        self.assertEqual((termo.versao, termo.conteudo), (conteudo.TERMO_LGPD_VERSAO, conteudo.TERMO_LGPD_CONTEUDO))

    def test_pagina_mostra_o_conteudo_da_versao_vigente_com_versao(self):
        VersaoTermo.objects.filter(tipo='LGPD').update(ativa=False)
        VersaoTermo.objects.create(
            tipo='LGPD', titulo='Política', versao='2.0', vigente_desde=timezone.localdate(),
            conteudo='Intro <script>x</script>.\n\n1. Primeira seção\n\n- item um\n- item dois',
        )
        resp = self.client.get(reverse('aranha:politica_privacidade'))
        self.assertContains(resp, 'Versão 2.0')
        self.assertContains(resp, '<h2>1. Primeira seção</h2>', html=False)
        self.assertContains(resp, '<li>item um</li>', html=False)
        self.assertContains(resp, 'Intro &lt;script&gt;x&lt;/script&gt;.')   # sanitizado
        self.assertNotContains(resp, '<script>x</script>')

    def test_texto_cobre_as_decisoes(self):
        texto = conteudo.TERMO_LGPD_CONTEUDO
        for trecho in ('18 anos ou mais', 'CPF, CEP e endereço', 'Não coletamos RG nem profissão',
                       'art. 11, I', 'revogar esse consentimento', 'PARAR ou SAIR', 'art. 20',
                       'Registros de auditoria, com endereço IP: 5 anos', 'servidas pelo próprio site',
                       'Zenvia', 'Railway', 'Meta', 'Sentry', 'jsDelivr', 'art. 33', 'ANPD'):
            with self.subTest(trecho=trecho):
                self.assertIn(trecho, texto)
        self.assertNotIn('fontes do Google', texto)
        self.assertNotIn('II, "f"', texto)   # a base dupla da saude saiu (decisao 32)
        self.assertNotIn('6 meses', texto)  # IP fica 5 anos (decisao 37)

    def test_mudar_o_texto_sem_versao_nova_muda_o_hash_da_prova(self):
        termo = VersaoTermo.lgpd_vigente()
        self.assertEqual(termo.sha256_conteudo, VersaoTermo(conteudo=conteudo.montar_politica()).sha256_conteudo)
        alterado = VersaoTermo(conteudo=conteudo.TERMO_LGPD_CONTEUDO + ' ')
        self.assertNotEqual(termo.sha256_conteudo, alterado.sha256_conteudo)


class CheckPrazosTests(TestCase):
    def test_padrao_bate_com_o_settings(self):
        for _rotulo, chave, padrao, _unidade, _c in conteudo.PRAZOS_RETENCAO:
            if chave:
                self.assertEqual(getattr(settings, chave), padrao, chave)
        self.assertEqual(aranha_checks.prazos_fora_do_termo(), [])

    def test_divergencia_entre_texto_e_settings_acusa_w016(self):
        with override_settings(RETENCAO_ATENDIMENTO_SAUDE_DIAS=365 * 25, DEBUG=False):
            self.assertEqual(aranha_checks.prazos_fora_do_termo(), ['RETENCAO_ATENDIMENTO_SAUDE_DIAS=9125'])
            ids = {e.id for e in aranha_checks.check_config_producao(databases=['default'])}
        self.assertIn('aranha.W016', ids)

    def test_formatar_prazo(self):
        self.assertEqual([conteudo.formatar_prazo(d) for d in (7300, 365, 90, 30)],
                         ['20 anos', '1 ano', '90 dias', '30 dias'])
        self.assertEqual(conteudo.formatar_prazo(24, 'horas'), '24 horas')


class ExportSemSaudeTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente(nome='Bia Export', telefone='17933335555')
        Prontuario.objects.create(cliente=self.cliente, alergias='Látex')

    def test_self_service_nao_leva_saude_e_traz_a_declaracao(self):
        dados = lgpd.exportar_dados(self.cliente, incluir_saude=False)
        for secao in lgpd.SECOES_SAUDE:
            self.assertNotIn(secao, dados)
        self.assertIn('e-mail cadastrado', dados['ficha_de_saude'])
        sobre = dados['sobre_o_tratamento']
        self.assertTrue(sobre['tratamentos'] and sobre['compartilhamento'])
        self.assertTrue(any(p.startswith('Ficha de avaliação') and '20 anos' in p for p in sobre['prazos_de_guarda']))
        self.assertNotIn('Látex', json.dumps(dados, ensure_ascii=False, default=str))

    def test_equipe_exporta_completo(self):
        dados = lgpd.LgpdService.exportar_dados_cliente(self.cliente)
        self.assertEqual(dados['prontuario']['alergias'], 'Látex')
        self.assertNotIn('ficha_de_saude', dados)

    def test_secoes_cobrem_toda_tabela_do_titular(self):
        cobertas = {t for _n, _f, tabelas, _s in lgpd.SECOES_DSAR for t in tabelas} | set(lgpd.FORA_DO_DSAR)
        titular = {t for t, classe in inv.TABELAS.items() if classe == inv.TITULAR}
        self.assertEqual(sorted(titular - cobertas), [])


@override_settings(RATELIMIT_ENABLE=False)
class MeusDadosViewTests(TestCase):
    URL = 'aranha:lgpd_meus_dados'

    def setUp(self):
        cache.clear()
        self.cliente = criar_cliente(nome='Joana Dados', telefone='17933336666')
        Prontuario.objects.create(cliente=self.cliente, alergias='Dipirona')

    def test_download_sem_saude(self):
        codigo, _ = CodigoOtp.gerar_sms('17933336666', proposito=CodigoOtp.PROPOSITO_DSAR)
        resp = self.client.post(reverse(self.URL), {'telefone': '(17) 93333-6666', 'codigo': codigo})
        self.assertIn('attachment', resp['Content-Disposition'])
        dados = json.loads(resp.content)
        self.assertNotIn('prontuario', dados)
        self.assertNotIn(b'Dipirona', resp.content)

    def test_usa_a_quota_e_o_cooldown_do_services_otp(self):
        with patch('aranha_estetica.views.publico.lgpd.otp_service.solicitar_otp_telefone',
                   return_value=(True, 'ok', 'SMS')) as solicitar:
            self.client.post(reverse(self.URL), {'telefone': '17 93333-6666'})
        solicitar.assert_called_once()
        self.assertEqual(solicitar.call_args.args[0], '17933336666')
        self.assertEqual(solicitar.call_args.kwargs['proposito'], CodigoOtp.PROPOSITO_DSAR)

    @override_settings(TURNSTILE_SECRET_KEY='segredo-teste')
    @patch('aranha_estetica.utils.captcha.verificar_turnstile', return_value=False)
    def test_sem_captcha_da_400(self, _verificar):
        resp = self.client.post(reverse(self.URL), {'telefone': '17933336666'})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(CodigoOtp.objects.filter(proposito=CodigoOtp.PROPOSITO_DSAR).exists())


class LimitePorTelefoneTests(TestCase):
    def test_telefone_com_e_sem_mascara_contam_no_mesmo_limite(self):
        cache.clear()
        criar_cliente(nome='Limite', telefone='17933337777')
        url = reverse('aranha:lgpd_meus_dados')
        formatos = ['17933337777', '(17) 93333-7777', '17 93333 7777', '+55 17 93333-7777', '17933337777']
        with patch('aranha_estetica.views.publico.lgpd.otp_service.solicitar_otp_telefone',
                   return_value=(False, 'aguarde', None)):
            for i, tel in enumerate(formatos):
                self.client = Client(REMOTE_ADDR=f'10.0.0.{i + 1}')   # IP diferente: so o telefone limita
                self.assertEqual(self.client.post(url, {'telefone': tel}).status_code, 200, tel)
            self.client = Client(REMOTE_ADDR='10.0.0.99')
            self.assertEqual(self.client.post(url, {'telefone': '17-93333-7777'}).status_code, 429)


@override_settings(RATELIMIT_ENABLE=False, OTP_LATENCIA_MINIMA_SEGUNDOS=0.8)
class LatenciaAntiEnumeracaoTests(TestCase):
    def test_cadastrado_e_desconhecido_demoram_o_mesmo_minimo(self):
        criar_cliente(nome='Cadastrada', telefone='17933338888')
        url = reverse('aranha:lgpd_meus_dados')
        with patch('aranha_estetica.utils.sms.requests.post') as zenvia:
            zenvia.return_value.status_code = 200
            for tel in ('17933338888', '17911110000'):
                inicio = time.perf_counter()
                self.client.post(url, {'telefone': tel})
                self.assertGreaterEqual(time.perf_counter() - inicio, 0.8, tel)

    def test_portal_cadastrado_e_desconhecido_demoram_o_mesmo_minimo(self):
        criar_cliente(nome='Portal Cadastrada', telefone='17933339999')
        url = reverse('aranha:meus_agendamentos_enviar_otp')
        with patch('aranha_estetica.utils.sms.requests.post') as zenvia:
            zenvia.return_value.status_code = 200
            for tel in ('17933339999', '17911112222'):
                inicio = time.perf_counter()
                resp = self.client.post(url, {'identificador': tel})
                self.assertIs(resp.json()['ok'], True, tel)   # mesma resposta nos dois casos
                self.assertGreaterEqual(time.perf_counter() - inicio, 0.8, tel)
