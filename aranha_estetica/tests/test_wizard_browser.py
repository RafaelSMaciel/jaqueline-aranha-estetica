"""Wizard de agendamento no navegador (Playwright + Chromium headless).

Regras que so existem no JS (wizard.js): obrigatoriedade da ficha de anamnese
e do consentimento de saude (rev_booking-01), mensagem do grupo de multipla
escolha junto da ficha (rev_booking-08) e calendario sem pular mes / sem
resposta AJAX fora de ordem (rev_booking-07).

Pulado quando o Playwright ou o Chromium nao estao instalados (ex.: imagem
Docker de producao, CI sem navegador).
"""
import json
import os
from datetime import datetime, timedelta
from unittest import SkipTest

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import Feriado, FormularioAnamnese

from .factories import criar_procedimento, criar_profissional

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depende do ambiente
    sync_playwright = None

TZ = 'America/Sao_Paulo'


@override_settings(RATELIMIT_ENABLE=False)
class WizardNavegadorTests(StaticLiveServerTestCase):
    serialized_rollback = True  # preserva dados das data migrations p/ os proximos testes

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise SkipTest('Playwright nao instalado')
        # Playwright sync mantem um event loop no thread do teste: o ORM do
        # proprio teste (setUp) precisa desta liberacao; o servidor roda em
        # outro thread e nao e afetado.
        cls._async_unsafe_antes = os.environ.get('DJANGO_ALLOW_ASYNC_UNSAFE')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
        cls._pw = sync_playwright().start()
        try:
            cls._browser = cls._pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - navegador ausente = pular
            cls._pw.stop()
            cls._restaurar_env()
            raise SkipTest(f'Chromium indisponivel: {exc}')
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._browser.close()
        cls._pw.stop()
        cls._restaurar_env()

    @classmethod
    def _restaurar_env(cls):
        if cls._async_unsafe_antes is None:
            os.environ.pop('DJANGO_ALLOW_ASYNC_UNSAFE', None)
        else:
            os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = cls._async_unsafe_antes

    def setUp(self):
        cache.clear()
        Feriado.objects.all().delete()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.context = self._browser.new_context(timezone_id=TZ, locale='pt-BR')
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)

    # ─── helpers ───
    def _abrir(self):
        url = f"{self.live_server_url}{reverse('aranha:agendamento_publico')}?procedimento={self.proc.pk}"
        self.page.goto(url)
        self.page.wait_for_selector('#step-2.active')

    def _ir_para_passo_3(self):
        """Escolhe um dia >= 2 dias a frente e o primeiro horario (1 profissional -> passo 3)."""
        self._abrir()
        minimo = (timezone.localdate() + timedelta(days=2)).isoformat()
        for _ in range(3):
            self.page.wait_for_selector('#cal-days-grid .cal-day')
            dias = [
                d for d in self.page.eval_on_selector_all(
                    '#cal-days-grid .cal-day.available', 'els => els.map(e => e.dataset.date)')
                if d >= minimo
            ]
            if dias:
                break
            with self.page.expect_response(lambda r: 'dias-disponiveis' in r.url):
                self.page.click('#cal-next')
        self.assertTrue(dias, 'nenhum dia disponivel no calendario')
        self.page.click(f'.cal-day[data-date="{dias[0]}"]')
        self.page.click('.slot-btn >> nth=0')
        self.page.wait_for_selector('#step-3.active')
        self.page.fill('#form-telefone', '17999998888')
        self.page.check('#form-aceite-politica')

    def _valido(self):
        return self.page.evaluate("document.getElementById('booking-form').checkValidity()")

    def _required(self, seletor):
        return self.page.eval_on_selector(seletor, 'el => el.required')

    def _ficha(self, obrigatorio, schema):
        return FormularioAnamnese.objects.create(
            nome='Anamnese padrão', tipo='ANAMNESE', escopo='GLOBAL',
            obrigatorio=obrigatorio, schema_json=schema,
        )

    # ─── rev_booking-01 ───
    def test_ficha_opcional_em_branco_nao_exige_resposta_nem_consentimento(self):
        form = self._ficha(False, [
            {'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante ou amamentando?', 'obrigatorio': True},
            {'key': 'obs', 'tipo': 'text', 'label': 'Observações'},
        ])
        self._ir_para_passo_3()
        sel_gestante = f'[data-form-id="{form.pk}"][data-field-key="gestante"]'
        sel_obs = f'[data-form-id="{form.pk}"][data-field-key="obs"]'

        self.assertTrue(self._valido())
        self.assertFalse(self._required(sel_gestante))
        self.assertFalse(self._required('#form-consent-saude'))

        # Respondeu algo: a ficha passa a valer e o consentimento (art. 11) e exigido
        self.page.select_option(sel_gestante, 'nao')
        self.assertTrue(self._required('#form-consent-saude'))
        self.assertFalse(self._valido())
        self.page.check('#form-consent-saude')
        self.assertTrue(self._valido())

        # Desfez a resposta: volta a ser opcional
        self.page.uncheck('#form-consent-saude')
        self.page.select_option(sel_gestante, '')
        self.assertFalse(self._required('#form-consent-saude'))
        self.assertTrue(self._valido())

        # Comecou pela pergunta opcional: a obrigatoria da mesma ficha passa a ser exigida
        self.page.fill(sel_obs, 'Pele sensível')
        self.assertTrue(self._required(sel_gestante))
        self.assertFalse(self._valido())

    def test_ficha_obrigatoria_exige_perguntas_obrigatorias(self):
        form = self._ficha(True, [
            {'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante?', 'obrigatorio': True},
        ])
        self._ir_para_passo_3()
        sel = f'[data-form-id="{form.pk}"][data-field-key="gestante"]'
        self.assertTrue(self._required(sel))
        self.assertFalse(self._valido())
        self.page.select_option(sel, 'sim')
        self.page.check('#form-consent-saude')
        self.assertTrue(self._valido())

    # ─── rev_booking-08 ───
    def test_grupo_obrigatorio_vazio_avisa_junto_da_ficha_com_foco(self):
        form = self._ficha(True, [
            {'key': 'areas', 'tipo': 'checkboxes', 'label': 'Áreas de interesse',
             'opcoes': ['Testa', 'Olhos'], 'obrigatorio': True},
        ])
        self._ir_para_passo_3()
        grupo = f'[data-grupo-form-id="{form.pk}"]'
        rotulo = self.page.eval_on_selector(
            grupo, "g => document.getElementById(g.getAttribute('aria-labelledby')).textContent")
        self.assertIn('Áreas de interesse', rotulo)

        url_antes = self.page.url
        self.page.evaluate("""() => {
            const b = document.getElementById('btn-confirmar');
            b.disabled = false;
            document.getElementById('booking-form').requestSubmit(b);
        }""")
        self.assertEqual(self.page.url, url_antes)  # submit barrado no navegador
        self.assertIn('Responda: Áreas de interesse', self.page.text_content('#anamnese-msg'))
        self.assertTrue(self.page.evaluate(
            f"document.querySelector('{grupo}').contains(document.activeElement)"))
        self.assertEqual(self.page.text_content('#otp-status').strip(), '')

        self.page.check(f'{grupo} input >> nth=0')
        self.assertEqual(self.page.text_content('#anamnese-msg'), '')

    # ─── rev_booking-07 ───
    def test_calendario_em_31_de_outubro_avanca_para_novembro(self):
        self.page.clock.set_fixed_time(datetime(2026, 10, 31, 12, 0))
        self._abrir()
        rotulo = lambda: self.page.text_content('#cal-month-label')  # noqa: E731
        self.assertEqual(rotulo(), 'Outubro 2026')
        self.page.click('#cal-next')
        self.assertEqual(rotulo(), 'Novembro 2026')
        self.page.click('#cal-next')
        self.assertEqual(rotulo(), 'Dezembro 2026')
        self.page.click('#cal-prev')
        self.page.click('#cal-prev')
        self.assertEqual(rotulo(), 'Outubro 2026')

    def test_resposta_atrasada_de_outro_mes_nao_pinta_o_calendario(self):
        self.page.clock.set_fixed_time(datetime(2026, 10, 15, 12, 0))
        presas = []

        def segurar_outubro(route):
            if 'mes=2026-10' in route.request.url:
                presas.append(route)
            else:
                route.continue_()

        self.page.route('**/ajax/dias-disponiveis/**', segurar_outubro)
        self._abrir()
        with self.page.expect_response(lambda r: 'mes=2026-11' in r.url):
            self.page.click('#cal-next')
        self.page.wait_for_selector('#cal-days-grid .cal-day')
        self.assertEqual(len(presas), 1)
        # Resposta de outubro chega DEPOIS da de novembro
        presas[0].fulfill(status=200, content_type='application/json',
                          body=json.dumps({'mes': '2026-10', 'dias_disponiveis': ['2026-10-20']}))
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.text_content('#cal-month-label'), 'Novembro 2026')
        self.assertEqual(self.page.eval_on_selector_all(
            '#cal-days-grid .cal-day[data-date^="2026-10"]', 'els => els.length'), 0)
        self.assertEqual(self.page.eval_on_selector_all(
            '#cal-days-grid .cal-day:not(.empty)', 'els => els.length'), 30)
