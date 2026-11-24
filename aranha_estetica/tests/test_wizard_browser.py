"""Wizard de agendamento no navegador (Playwright + Chromium headless).

Regras que so existem no JS (wizard.js): obrigatoriedade da ficha de anamnese
e do consentimento de saude (rev_booking-01), mensagem do grupo de multipla
escolha junto da ficha (rev_booking-08), calendario sem pular mes / sem
resposta AJAX fora de ordem (rev_booking-07), selecao de varios tratamentos com um
calendario para cada, erro ao lado do campo e ficha sim/nao com detalhe condicional (2026-11-24).

Pulado quando o Playwright, o Chromium ou o bundle do Vite faltam (ex.: imagem Docker de
producao). No CI (job navegador) roda com EXIGE_NAVEGADOR=1: faltou algo, reprova em vez de pular.
Local: `npm run build`, `playwright install chromium` e TESTE_VITE_BUILD=1 (settings/test.py).
"""
import json
import os
from datetime import datetime, timedelta
from unittest import SkipTest

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import FormularioAnamnese

from .factories import criar_procedimento, criar_profissional, limpar_feriados

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depende do ambiente
    sync_playwright = None

TZ = 'America/Sao_Paulo'


def _pular(motivo):
    if os.environ.get('EXIGE_NAVEGADOR', '').strip() == '1':
        raise RuntimeError(f'EXIGE_NAVEGADOR=1: {motivo}')
    raise SkipTest(motivo)


@override_settings(RATELIMIT_ENABLE=False)
class WizardNavegadorTests(StaticLiveServerTestCase):
    serialized_rollback = True  # preserva dados das data migrations p/ os proximos testes

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            _pular('Playwright nao instalado')
        if settings.DJANGO_VITE['default']['dev_mode']:
            # dev_mode: wizard.js viria do Vite dev server (localhost:5173), fora do live server
            _pular('sem bundle do Vite: npm run build e TESTE_VITE_BUILD=1')
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
        cache.clear()
        limpar_feriados()
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
        """Escolhe um dia >= 2 dias a frente e o primeiro horario, que ja leva ao passo 3."""
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
        self.page.click('.slot-btn >> nth=0')   # 1 profissional: o horario ja leva ao passo 3
        self.page.wait_for_selector('#step-3.active')
        self.page.fill('#form-telefone', '17999998888')
        self.page.check('#form-aceite-politica')

    def _drenar(self):
        """Espera o JS terminar de tratar a resposta (promessas + 2 quadros), sem sleep fixo."""
        self.page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')

    def _json(self, route, corpo, status=200):
        route.fulfill(status=status, content_type='application/json', body=json.dumps(corpo))

    def _erros(self):
        """Tenta enviar e devolve os ids dos erros que o wizard pintou ao lado dos campos — o
        formulario e novalidate: quem valida e o wizard.js. O envio de verdade e barrado aqui (um
        listener depois do do wizard), senao sem erro a pagina navegaria."""
        return self.page.evaluate("""() => {
            const form = document.getElementById('booking-form');
            const barrar = (e) => e.preventDefault();
            form.addEventListener('submit', barrar);
            form.dataset.enviando = '';
            form.requestSubmit();
            form.removeEventListener('submit', barrar);
            form.dataset.enviando = '';
            return [...form.querySelectorAll('.form-erro:not([hidden])')].map(e => e.id);
        }""")

    def _valido(self):
        return not self._erros()

    def _opcao(self, form, key, valor):
        """Seletor do Sim/Nao (radio) de uma pergunta bool da ficha."""
        return f'[data-form-id="{form.pk}"][data-field-key="{key}"][value="{valor}"]'

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
        sel_obs = f'[data-form-id="{form.pk}"][data-field-key="obs"]'
        erro_gestante = f'erro-an-{form.pk}-gestante'

        self.assertTrue(self._valido())
        self.assertFalse(self._required('#form-consent-saude'))

        # Comecou pela pergunta opcional: a obrigatoria da mesma ficha passa a ser exigida
        self.page.fill(sel_obs, 'Pele sensível')
        self.assertIn(erro_gestante, self._erros())

        # Desfez a resposta: volta a ser opcional
        self.page.fill(sel_obs, '')
        self.assertFalse(self._required('#form-consent-saude'))
        self.assertTrue(self._valido())

        # Respondeu a obrigatoria: a ficha passa a valer e o consentimento (art. 11) e exigido
        self.page.check(self._opcao(form, 'gestante', 'nao'))
        self.assertTrue(self._required('#form-consent-saude'))
        self.assertEqual(self._erros(), ['erro-consent-saude'])
        self.page.check('#form-consent-saude')
        self.assertTrue(self._valido())

    def test_ficha_obrigatoria_exige_perguntas_obrigatorias(self):
        form = self._ficha(True, [
            {'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante?', 'obrigatorio': True},
        ])
        self._ir_para_passo_3()
        self.assertEqual(self._erros(), [f'erro-an-{form.pk}-gestante'])
        self.assertIn('Escolha Sim ou Não', self.page.text_content(f'#erro-an-{form.pk}-gestante'))
        self.page.check(self._opcao(form, 'gestante', 'sim'))
        self.assertEqual(self._erros(), ['erro-consent-saude'])
        self.page.check('#form-consent-saude')
        self.assertTrue(self._valido())

    def test_detalhe_so_aparece_com_sim_e_nao_aceita_resposta_de_enfeite(self):
        """Pergunta condicional ("se"): o texto so e pedido como detalhe de um Sim — e '...' nao e resposta."""
        form = self._ficha(True, [
            {'key': 'tem', 'tipo': 'bool', 'label': 'Tem alergia?', 'obrigatorio': True},
            {'key': 'qual', 'tipo': 'text', 'label': 'A que você tem alergia?', 'obrigatorio': True, 'se': 'tem'},
        ])
        self._ir_para_passo_3()
        detalhe = f'[data-form-id="{form.pk}"][data-field-key="qual"]'
        erro_qual = f'erro-an-{form.pk}-qual'
        self.assertFalse(self.page.is_visible(detalhe))

        self.page.check(self._opcao(form, 'tem', 'sim'))
        self.assertTrue(self.page.is_visible(detalhe))
        self.assertEqual(self._erros(), [erro_qual, 'erro-consent-saude'])
        self.page.fill(detalhe, '...')
        self.assertIn(erro_qual, self._erros())
        self.assertIn('resposta com palavras', self.page.text_content(f'#{erro_qual}'))
        self.page.fill(detalhe, 'Lidocaína')
        self.page.check('#form-consent-saude')
        self.assertTrue(self._valido())

        # Trocou p/ Nao: o detalhe some e o que estava escrito nao e enviado
        self.page.check(self._opcao(form, 'tem', 'nao'))
        self.assertFalse(self.page.is_visible(detalhe))
        self.assertEqual(self.page.input_value(detalhe), '')
        self.assertTrue(self._valido())
        enviado = json.loads(self.page.input_value('#anamneseRespostasJson'))
        self.assertEqual(enviado, {str(form.pk): {'tem': 'nao'}})

    # ─── rev_booking-08 ───
    def test_grupo_obrigatorio_vazio_avisa_junto_da_ficha_com_foco(self):
        self._ficha(True, [
            {'key': 'areas', 'tipo': 'checkboxes', 'label': 'Áreas de interesse',
             'opcoes': ['Testa', 'Olhos'], 'obrigatorio': True},
        ])
        self._ir_para_passo_3()
        grupo = '.ficha-campo[data-key="areas"] [data-grupo]'
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
        with self.page.expect_event('requestfinished', lambda r: 'mes=2026-10' in r.url):
            presas[0].fulfill(status=200, content_type='application/json',
                              body=json.dumps({'ok': True, 'mes': '2026-10', 'dias_disponiveis': ['2026-10-20']}))
        self._drenar()
        self.assertEqual(self.page.text_content('#cal-month-label'), 'Novembro 2026')
        self.assertEqual(self.page.eval_on_selector_all(
            '#cal-days-grid .cal-day[data-date^="2026-10"]', 'els => els.length'), 0)
        self.assertEqual(self.page.eval_on_selector_all(
            '#cal-days-grid .cal-day:not(.empty)', 'els => els.length'), 30)

    # ─── lista de espera no dia sem horario ───
    def test_dia_sem_horario_oferece_a_lista_de_espera(self):
        self.page.route('**/ajax/horarios-disponiveis/**', lambda route: self._json(route, {'ok': True, 'horarios': []}))
        self._abrir()
        self.page.wait_for_selector('#cal-days-grid .cal-day.available')
        dia = self.page.eval_on_selector('#cal-days-grid .cal-day.available', 'el => el.dataset.date')
        self.page.click(f'.cal-day[data-date="{dia}"]')
        link = self.page.wait_for_selector('#lista-espera-link')
        self.assertEqual(link.text_content(), 'Avise-me se vagar')
        href = link.get_attribute('href')
        self.assertIn(reverse('aranha:lista_espera_publica'), href)
        self.assertIn(f'procedimento={self.proc.pk}', href)
        self.assertIn(f'data={dia}', href)

    # ─── D16: o agendamento e da titular do celular ───
    def test_cadastro_existente_deixa_o_nome_so_leitura(self):
        self.page.route('**/agendamento/otp/solicitar/', lambda route: self._json(route, {'ok': True, 'canal': 'SMS'}))
        self.page.route('**/agendamento/otp/verificar/', lambda route: self._json(route, {
            'ok': True, 'aviso': '', 'aviso_mensagem': '',
            'prefill': {'nome': 'Titular do Celular', 'telefone': '17999998888', 'email': '',
                        'data_nascimento': '1990-01-01', 'consents': {}},
        }))
        self._ir_para_passo_3()
        self.assertTrue(self.page.eval_on_selector('#nome-titular-aviso', 'el => el.hidden'))
        self.page.click('#btn-enviar-otp')
        self.page.wait_for_selector('#otp-field', state='visible')
        self.page.fill('#otp-codigo', '123456')
        self.page.click('#btn-verificar-otp')
        self.page.wait_for_selector('#nome-titular-aviso', state='visible')
        self.assertEqual(self.page.input_value('#form-nome'), 'Titular do Celular')
        self.assertTrue(self.page.eval_on_selector('#form-nome', 'el => el.readOnly'))
        self.assertIn('outra pessoa', self.page.text_content('#nome-titular-aviso'))

    # ─── D14: ficha em dia nao e pedida de novo ───
    def test_ficha_em_dia_vira_aviso_com_atualizar(self):
        form = self._ficha(True, [
            {'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante?', 'obrigatorio': True},
        ])
        self.page.route('**/agendamento/otp/solicitar/', lambda route: self._json(route, {'ok': True, 'canal': 'SMS'}))
        self.page.route('**/agendamento/otp/verificar/', lambda route: self._json(route, {
            'ok': True, 'aviso': '', 'aviso_mensagem': '', 'prefill': None,
            'fichas_em_dia': {str(form.pk): '10/03/2026'},
        }))
        self._ir_para_passo_3()
        sel = self._opcao(form, 'gestante', 'sim')
        erro = f'erro-an-{form.pk}-gestante'
        self.assertIn(erro, self._erros())   # antes do SMS: obrigatoria
        self.page.click('#btn-enviar-otp')
        self.page.wait_for_selector('#otp-field', state='visible')
        self.page.fill('#otp-codigo', '123456')
        self.page.click('#btn-verificar-otp')
        aviso = self.page.wait_for_selector('.ficha-em-dia')
        self.assertIn('Sua ficha de 10/03/2026 está em dia', aviso.text_content())
        self.assertNotIn(erro, self._erros())
        self.assertFalse(self.page.is_visible(sel))
        self.page.click('.ficha-atualizar')
        self.assertTrue(self.page.is_visible(sel))
        self.page.check(sel)   # respondeu: o consentimento de saude volta a valer
        self.assertTrue(self._required('#form-consent-saude'))

    def test_codigo_incorreto_mostra_a_mensagem_do_servidor(self):
        self.page.route('**/agendamento/otp/solicitar/', lambda route: self._json(route, {'ok': True, 'canal': 'SMS'}))
        self.page.route('**/agendamento/otp/verificar/', lambda route: self._json(route, {
            'ok': False, 'codigo': 'incorreto', 'mensagem': 'Código incorreto. Tentativas restantes: 4', 'restantes': 4,
        }, status=400))
        self._ir_para_passo_3()
        self.page.click('#btn-enviar-otp')
        self.page.wait_for_selector('#otp-field', state='visible')
        self.page.fill('#otp-codigo', '123456')
        with self.page.expect_response(lambda r: 'otp/verificar' in r.url):
            self.page.click('#btn-verificar-otp')
        self._drenar()
        self.assertEqual(self.page.text_content('#otp-msg'), 'Código incorreto. Tentativas restantes: 4')

    # ─── tema: claro por padrao, escuro so por escolha (cookie) ───
    def test_card_selecionado_legivel_no_tema_escuro(self):
        escuro = self._browser.new_context(timezone_id=TZ, locale='pt-BR')
        escuro.add_cookies([{'name': 'tema', 'value': 'escuro', 'url': self.live_server_url}])
        self.addCleanup(escuro.close)
        pagina = escuro.new_page()
        pagina.goto(f"{self.live_server_url}{reverse('aranha:agendamento_publico')}")
        self.assertEqual(pagina.eval_on_selector('html', 'el => el.dataset.theme'), 'escuro')
        pagina.click(f'.proc-card[data-proc-id="{self.proc.pk}"]')
        texto, fundo = pagina.eval_on_selector(
            '.proc-card.selected', """card => [getComputedStyle(card.querySelector('.proc-name')).color,
                                              getComputedStyle(card).backgroundColor]""")
        self.assertNotEqual(texto, fundo)

    def test_sistema_em_modo_escuro_continua_no_tema_claro(self):
        """O padrao do site e o claro: nao segue mais o tema do sistema (so o cookie `tema`)."""
        sistema_escuro = self._browser.new_context(timezone_id=TZ, locale='pt-BR', color_scheme='dark')
        self.addCleanup(sistema_escuro.close)
        pagina = sistema_escuro.new_page()
        pagina.goto(f"{self.live_server_url}{reverse('aranha:agendamento_publico')}")
        tema, esquema, fundo = pagina.evaluate("""() => [
            document.documentElement.dataset.theme || '',
            getComputedStyle(document.documentElement).colorScheme,
            getComputedStyle(document.body).backgroundColor,
        ]""")
        self.assertEqual(tema, '')
        self.assertEqual(esquema, 'light')
        vermelho, verde, azul = [int(v) for v in fundo[fundo.index('(') + 1:fundo.index(')')].split(',')[:3]]
        self.assertGreater(min(vermelho, verde, azul), 200, fundo)   # fundo claro

    # ─── varios tratamentos no mesmo pedido ───
    def _abrir_lista(self):
        self.page.goto(f"{self.live_server_url}{reverse('aranha:agendamento_publico')}")
        self.page.wait_for_selector('#step-1.active')

    def _primeiro_dia(self, bloco):
        """Clica no 1o dia >= 2 dias a frente do calendario do bloco (avanca o mes se precisar)."""
        minimo = (timezone.localdate() + timedelta(days=2)).isoformat()
        for _ in range(3):
            bloco.locator('.cal-day').first.wait_for()
            dias = [d for d in bloco.locator('.cal-day.available').evaluate_all('els => els.map(e => e.dataset.date)')
                    if d >= minimo]
            if dias:
                break
            with self.page.expect_response(lambda r: 'dias-disponiveis' in r.url):
                bloco.locator('.cal-nav-btn').nth(1).click()
        self.assertTrue(dias, 'nenhum dia disponivel no calendario')
        bloco.locator(f'.cal-day[data-date="{dias[0]}"]').click()
        return dias[0]

    def test_dois_tratamentos_um_calendario_para_cada_e_um_pedido_so(self):
        outro = criar_procedimento('Drenagem', duracao=60, profissional=self.prof)
        self._abrir_lista()
        self.assertTrue(self.page.is_hidden('#carrinho-barra'))
        self.page.click(f'.proc-card[data-proc-id="{self.proc.pk}"]')
        # escolher NAO avanca sozinho: da p/ somar outro tratamento; o Continuar fica na barra
        self.assertTrue(self.page.is_visible('#step-1.active'))
        self.assertIn('1 tratamento', self.page.text_content('#carrinho-titulo'))
        self.page.click(f'.proc-card[data-proc-id="{outro.pk}"]')
        self.assertIn('2 tratamentos', self.page.text_content('#carrinho-titulo'))
        self.assertEqual(self.page.locator('.proc-card[aria-pressed="true"]').count(), 2)
        self.page.click('#btn-continuar-1')
        self.page.wait_for_selector('#step-2.active')

        blocos = self.page.locator('.agenda-item')
        self.assertEqual(blocos.count(), 2)
        self.assertEqual(blocos.nth(0).get_attribute('data-estado'), 'aberto')
        self.assertEqual(blocos.nth(1).get_attribute('data-estado'), 'pendente')
        self.assertTrue(self.page.is_disabled('#btn-continuar-2'))

        dia = self._primeiro_dia(blocos.nth(0))
        with self.page.expect_response(lambda r: 'horarios-disponiveis' in r.url and 'carrinho' in r.url):
            blocos.nth(0).locator('.slot-btn').first.click()
        # 1o pronto: recolhe com o resumo e o 2o abre, ja no mesmo dia
        self.page.wait_for_selector('.agenda-item >> nth=1 >> .slot-btn')
        self.assertEqual(blocos.nth(0).get_attribute('data-estado'), 'pronto')
        self.assertEqual(blocos.nth(1).get_attribute('data-estado'), 'aberto')
        self.assertEqual(blocos.nth(1).locator('.cal-day.selected').get_attribute('data-date'), dia)
        primeiro = self.page.evaluate("JSON.parse(sessionStorage.getItem('wizard_estado')).itens[0].slot.horario")
        horarios_do_segundo = blocos.nth(1).locator('.slot-btn').all_text_contents()
        # mesma profissional, mesmo dia: o horario do 1o tratamento nao e oferecido p/ o 2o
        self.assertNotIn(primeiro, horarios_do_segundo)
        self.assertEqual(blocos.nth(1).locator('.slot-btn--seguida').count(), 1)

        blocos.nth(1).locator('.slot-btn--seguida').click()
        self.page.wait_for_selector('#step-3.active')
        self.assertIn('2 agendamentos', self.page.text_content('#step-3-titulo'))
        self.assertTrue(self.page.is_hidden('#recap-unico'))
        self.assertEqual(self.page.locator('#recap-varios .recap-item').count(), 2)
        extras = json.loads(self.page.input_value('#form-itens-extras'))
        self.assertEqual(len(extras), 1)
        self.assertEqual(
            {int(self.page.input_value('#form-procedimento')), int(extras[0]['procedimento'])},
            {self.proc.pk, outro.pk},
        )

        # Voltar (no topo da etapa) leva aos calendarios, com as escolhas guardadas
        self.page.click('#step-3 .btn-back-step--topo')
        self.page.wait_for_selector('#step-2.active')
        self.assertFalse(self.page.is_disabled('#btn-continuar-2'))
        self.assertEqual(self.page.locator('.agenda-item[data-estado="pronto"]').count() +
                         self.page.locator('.agenda-item[data-estado="aberto"]').count(), 2)

    def test_limite_de_tratamentos_por_pedido(self):
        from aranha_estetica.constants import MAX_TRATAMENTOS_POR_PEDIDO

        extras = [criar_procedimento(f'Extra {i}', profissional=self.prof) for i in range(MAX_TRATAMENTOS_POR_PEDIDO)]
        self._abrir_lista()
        self.page.click(f'.proc-card[data-proc-id="{self.proc.pk}"]')
        for proc in extras:
            self.page.click(f'.proc-card[data-proc-id="{proc.pk}"]')
        self.assertEqual(self.page.locator('.proc-card.selected').count(), MAX_TRATAMENTOS_POR_PEDIDO)
        self.assertTrue(self.page.is_visible('#carrinho-limite'))
        # tirar um libera a escolha de outro
        self.page.click(f'.proc-card[data-proc-id="{self.proc.pk}"]')
        self.assertTrue(self.page.is_hidden('#carrinho-limite'))
        self.assertEqual(self.page.locator('.proc-card.selected').count(), MAX_TRATAMENTOS_POR_PEDIDO - 1)

    # ─── regras de qualidade: o erro aparece ao lado do campo ───
    def test_celular_que_nao_existe_explica_o_motivo(self):
        self._ir_para_passo_3()
        for numero, trecho in (('20912345678', 'DDD 20 não existe'), ('11999999999', 'não parece um celular'),
                               ('1133334444', 'Número incompleto'), ('11833334444', 'começando com 9')):
            with self.subTest(numero=numero):
                self.page.fill('#form-telefone', numero)
                self.page.click('#btn-enviar-otp')
                self.assertIn(trecho, self.page.text_content('#otp-status'))
                self.assertTrue(self.page.is_hidden('#otp-field'))

    def test_dados_pessoais_invalidos_mostram_o_erro_no_campo(self):
        self.page.route('**/agendamento/otp/solicitar/', lambda route: self._json(route, {'ok': True, 'canal': 'SMS'}))
        self.page.route('**/agendamento/otp/verificar/', lambda route: self._json(route, {
            'ok': True, 'aviso': '', 'aviso_mensagem': '', 'prefill': None, 'fichas_em_dia': {},
        }))
        self._ir_para_passo_3()
        self.page.click('#btn-enviar-otp')
        self.page.wait_for_selector('#otp-field', state='visible')
        self.page.fill('#otp-codigo', '123456')
        self.page.click('#btn-verificar-otp')
        self.page.wait_for_selector('#form-nome', state='visible')

        self.assertEqual(self._erros(), ['erro-nome', 'erro-nascimento'])
        self.assertIn('2 itens', self.page.text_content('#form-erro-geral'))
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'form-nome')

        hoje = timezone.localdate()
        casos = (
            ('#form-nome', 'Maria', '#erro-nome', 'nome e sobrenome'),
            ('#form-nome', 'Maria 123', '#erro-nome', 'só letras'),
            ('#form-nascimento', hoje.isoformat(), '#erro-nascimento', '18 anos'),
            ('#form-nascimento', '1850-01-01', '#erro-nascimento', 'ano de nascimento'),
            ('#form-email', 'maria@', '#erro-email', 'E-mail inválido'),
        )
        for campo, valor, erro, trecho in casos:
            with self.subTest(campo=campo, valor=valor):
                self.page.fill(campo, valor)
                self.page.dispatch_event(campo, 'blur')
                self.assertIn(trecho, self.page.text_content(erro))
                self.assertEqual(self.page.get_attribute(campo, 'aria-invalid'), 'true')

        # corrigiu: o erro some na hora e o envio passa
        self.page.fill('#form-nome', 'Maria Teste')
        self.page.fill('#form-nascimento', '1990-06-15')
        self.page.fill('#form-email', 'maria@example.com')
        self.assertTrue(self.page.is_hidden('#erro-nome'))
        self.assertTrue(self._valido())
        self.assertTrue(self.page.is_hidden('#form-erro-geral'))
