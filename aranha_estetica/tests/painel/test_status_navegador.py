"""Select de status da agenda no navegador (Playwright + Chromium headless): o JS saiu do template
para static/src/js/painel-status.js (modulo do Vite) e precisa achar a rota (data-url), o token CSRF
(meta da casca) e o contrato JSON (codigo termo_pendente -> confirmacao e reenvio com override).

Pulado sem Playwright/Chromium ou sem o bundle do Vite (npm run build + TESTE_VITE_BUILD=1), como o
teste do wizard; no CI (EXIGE_NAVEGADOR=1) reprova em vez de pular."""
import os
from unittest import SkipTest

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import Atendimento, LogAuditoria, Usuario, VersaoTermo

from ..factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional, ja_comecou

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depende do ambiente
    sync_playwright = None


def _pular(motivo):
    if os.environ.get('EXIGE_NAVEGADOR', '').strip() == '1':
        raise RuntimeError(f'EXIGE_NAVEGADOR=1: {motivo}')
    raise SkipTest(motivo)


@override_settings(RATELIMIT_ENABLE=False, ADMIN_2FA_OBRIGATORIO=False)
class SelectDeStatusNavegadorTests(StaticLiveServerTestCase):
    serialized_rollback = True  # preserva dados das data migrations p/ os proximos testes

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            _pular('Playwright nao instalado')
        if settings.DJANGO_VITE['default']['dev_mode']:
            _pular('sem bundle do Vite: npm run build e TESTE_VITE_BUILD=1')
        cls._async_unsafe_antes = os.environ.get('DJANGO_ALLOW_ASYNC_UNSAFE')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'   # ORM do teste com o event loop do Playwright
        cls._pw = sync_playwright().start()
        try:
            cls._browser = cls._pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - navegador ausente = pular
            cls._pw.stop()
            cls._restaurar_env()
            _pular(f'Chromium indisponivel: {exc}')
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
        admin = Usuario.objects.create_superuser(email='nav-p06@test.com', password='senha-forte-123', nome='Nav')
        self.client.force_login(admin)
        self.prof = criar_profissional('Dra. Navegador')
        self.proc = criar_procedimento(profissional=self.prof)
        self.context = self._browser.new_context(timezone_id='America/Sao_Paulo', locale='pt-BR')
        self.addCleanup(self.context.close)
        self.context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                                   'value': self.client.cookies[settings.SESSION_COOKIE_NAME].value,
                                   'url': self.live_server_url}])
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        self.dialogos = []

        def responder(dialogo):
            self.dialogos.append(dialogo.message)
            dialogo.accept()
        self.page.on('dialog', responder)

    def _agenda(self):
        self.page.goto(f"{self.live_server_url}{reverse('aranha:painel_agendamentos')}")

    def _escolher(self, at, status):
        with self.page.expect_navigation():   # sucesso recarrega a pagina
            self.page.select_option(f'select[data-atend-id="{at.pk}"]', status)

    def test_confirmar_pelo_select(self):
        at = criar_atendimento(criar_cliente('Nara'), self.prof, self.proc)
        self._agenda()
        self._escolher(at, 'CONFIRMADO')
        self.assertEqual(Atendimento.objects.get(pk=at.pk).status, 'CONFIRMADO')
        self.assertEqual(self.dialogos, ['Alterar o status para "Confirmar presença"?'])

    def test_termo_publicado_depois_de_abrir_pede_override(self):
        at = ja_comecou(criar_atendimento(criar_cliente('Olga'), self.prof, self.proc))
        self._agenda()
        VersaoTermo.objects.create(tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo', conteudo='R.',
                                   versao='1.0', vigente_desde=timezone.localdate())
        self._escolher(at, 'REALIZADO')   # 409 termo_pendente -> confirma -> reenvia com sem_termo
        self.assertEqual(Atendimento.objects.get(pk=at.pk).status, 'REALIZADO')
        self.assertEqual(len(self.dialogos), 2)
        self.assertIn('ainda não aceitou o termo', self.dialogos[1])
        self.assertTrue(LogAuditoria.objects.filter(acao='Realizado sem termo aceito', registro_id=at.pk).exists())

    def test_calendario_carrega_eventos_pelo_modulo(self):
        """painel-calendario.js le a rota do data-url-eventos e monta o FullCalendar (npm, no bundle
        do Vite: sem rede externa) com o CSS injetado no <style data-fullcalendar> do nonce."""
        criar_atendimento(criar_cliente('Rita Calendário'), self.prof, self.proc)
        erros, console = [], []
        self.page.on('pageerror', lambda exc: erros.append(str(exc)))
        self.page.on('console', lambda m: console.append(m.text) if m.type == 'error' else None)
        externos = []
        self.page.on('request', lambda r: externos.append(r.url) if not r.url.startswith(self.live_server_url) else None)
        with self.page.expect_response(lambda r: reverse('aranha:admin_calendar_events') in r.url) as resp:
            self.page.goto(f"{self.live_server_url}{reverse('aranha:admin_calendar')}")
        self.assertEqual(resp.value.status, 200)
        self.page.wait_for_selector('.fc-event')
        self.assertIn('Rita Calendário', self.page.text_content('#calendar'))
        self.assertEqual(self.page.get_attribute('.fc-prev-button', 'aria-label'), 'Período anterior')
        # o CSS do FullCalendar entrou (sem 'unsafe-inline'): a grade tem layout de verdade
        self.assertGreater(self.page.evaluate("document.querySelector('style[data-fullcalendar]').sheet.cssRules.length"), 50)
        self.assertEqual(self.page.get_attribute('table.fc-scrollgrid', 'role'), 'presentation')
        self.assertEqual((erros, console, externos), ([], [], []))

    def test_barra_do_lote_aparece_com_pendente_marcado(self):
        pendente = criar_atendimento(criar_cliente('Paula'), self.prof, self.proc, status='PENDENTE')
        self._agenda()
        barra = self.page.locator('#bulkToolbar')
        self.assertFalse(barra.is_visible())
        self.page.check(f'.bulk-checkbox[value="{pendente.pk}"]')
        self.assertTrue(barra.is_visible())
        self.assertEqual(self.page.text_content('#bulkCount'), '1 selecionado(s)')
