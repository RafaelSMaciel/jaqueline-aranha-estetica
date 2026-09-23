"""Regressao da auditoria pre-producao — autenticacao/2FA da equipe.

Cobre: bypass do 2FA na tela de seguranca, rotas publicas do two_factor,
Django admin via fluxo 2FA do painel, 2FA obrigatorio p/ ADMIN + /api/,
login de PROFISSIONAL, reset de senha (500 + e-mail), logout so via POST,
telas de usuario (500 form_data, form aninhado, auto-rebaixamento) e o
Django admin (senha em texto puro, hard-delete, auditoria apagavel, prova de
consentimento e dado clinico so-leitura) e QR do 2FA sem Pillow.
"""
import base64
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

from django.contrib import admin as django_admin
from django.core import mail
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase as _TestCase, override_settings
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from aranha_estetica.models import (
    AceiteTermo, AnotacaoSessao, Cliente, LogAuditoria, Profissional, Prontuario, Usuario,
    VersaoTermo,
)

SENHA = 'Senha-Forte-2026!'

_AUSENTE = object()


@contextmanager
def _sem_pillow():
    """Simula a imagem de producao (python:3.12-slim SEM Pillow).

    None em sys.modules faz `import PIL`/`from qrcode.image.pil import ...`
    levantar ImportError — o caminho PNG padrao do qrcode quebraria (500).
    """
    bloqueados = ('PIL', 'PIL.Image', 'PIL.ImageDraw', 'qrcode.image.pil')
    salvos = {nome: sys.modules.get(nome, _AUSENTE) for nome in bloqueados}
    try:
        for nome in bloqueados:
            sys.modules[nome] = None
        yield
    finally:
        for nome, mod in salvos.items():
            if mod is _AUSENTE:
                sys.modules.pop(nome, None)
            else:
                sys.modules[nome] = mod


def _qr_svg(html):
    """Decodifica o QR (data URI SVG) da tela de cadastro do 2FA."""
    m = re.search(r'data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)', html)
    return base64.b64decode(m.group(1)).decode() if m else ''


class TestCase(_TestCase):
    # ratelimit (key='user'/'ip') usa o cache: pk/IP se repetem entre testes
    def setUp(self):
        cache.clear()
        super().setUp()


def _token(device):
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time()
    return f'{totp.token():0{device.digits}d}'


def _admin(email='adm@test.com', **kw):
    return Usuario.objects.create_user(
        email=email, password=SENHA, nome=kw.pop('nome', 'Admin Teste'),
        papel=Usuario.PAPEL_ADMIN, **kw,
    )


def _profissional_user(email='prof@test.com', ativo_prof=True):
    prof = Profissional.objects.create(nome='Dra. Portal', ativo=ativo_prof)
    user = Usuario.objects.create_user(
        email=email, password=SENHA, nome='Dra. Portal',
        papel=Usuario.PAPEL_PROFISSIONAL, profissional=prof,
    )
    return user, prof


def _com_totp(user):
    return TOTPDevice.objects.create(user=user, name='totp-teste', confirmed=True)


class _FormAninhado(HTMLParser):
    def __init__(self):
        super().__init__()
        self.profundidade = 0
        self.aninhado = False

    def handle_starttag(self, tag, attrs):
        if tag == 'form':
            self.profundidade += 1
            if self.profundidade > 1:
                self.aninhado = True

    def handle_endtag(self, tag):
        if tag == 'form':
            self.profundidade -= 1


# ─── security-01: bypass do 2FA em /painel/seguranca/2fa/ ───

class Bypass2FATests(TestCase):
    def setUp(self):
        super().setUp()
        self.admin = _admin()
        self.device = _com_totp(self.admin)
        self.url = reverse('aranha:admin_2fa_setup')

    def test_sessao_so_com_senha_nao_desativa_2fa(self):
        c = Client()
        c.force_login(self.admin)
        resp = c.post(self.url, {'acao': 'desativar'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('aranha:admin_2fa_challenge'), resp['Location'])
        self.assertTrue(TOTPDevice.objects.filter(pk=self.device.pk).exists())
        # e o painel continua exigindo o desafio
        self.assertEqual(c.get(reverse('aranha:painel_overview')).status_code, 302)

    def test_sessao_so_com_senha_nao_troca_device(self):
        c = Client()
        c.force_login(self.admin)
        c.post(self.url, {'acao': 'gerar'})
        self.assertFalse(TOTPDevice.objects.filter(user=self.admin, confirmed=False).exists())

    def test_get_setup_com_device_exige_challenge(self):
        c = Client()
        c.force_login(self.admin)
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('aranha:admin_2fa_challenge'), resp['Location'])

    @override_settings(ADMIN_2FA_OBRIGATORIO=False)
    def test_verificado_desativa_so_com_codigo_valido(self):
        c = Client()
        c.force_login(self.admin)
        c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(self.device)})
        # sem codigo: recusa
        c.post(self.url, {'acao': 'desativar', 'token': ''})
        self.assertTrue(TOTPDevice.objects.filter(pk=self.device.pk).exists())
        # com backup valido: remove TOTP e backups
        static = StaticDevice.objects.create(user=self.admin, name='backup', confirmed=True)
        StaticToken.objects.create(device=static, token='abcd1234')
        c.post(self.url, {'acao': 'desativar', 'token': 'abcd1234'})
        self.assertFalse(TOTPDevice.objects.filter(user=self.admin).exists())
        self.assertFalse(StaticDevice.objects.filter(user=self.admin).exists())

    @override_settings(ADMIN_2FA_OBRIGATORIO=True)
    def test_admin_obrigatorio_trocar_aparelho_exige_novo_cadastro(self):
        c = Client()
        c.force_login(self.admin)
        c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(self.device)})
        static = StaticDevice.objects.create(user=self.admin, name='backup', confirmed=True)
        StaticToken.objects.create(device=static, token='zzzz9999')
        c.post(self.url, {'acao': 'desativar', 'token': 'zzzz9999'})
        resp = c.get(reverse('aranha:painel_overview'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.url, resp['Location'])


# ─── security-02 / security-14: rotas two_factor e Django admin ───

class RotasTwoFactorEAdminTests(TestCase):
    def test_rotas_account_nao_publicadas(self):
        c = Client()
        for url in ('/account/login/', '/account/two_factor/setup/', '/account/two_factor/'):
            with self.subTest(url=url):
                self.assertEqual(c.get(url).status_code, 404)

    def test_django_admin_alcancavel_apos_2fa_do_painel(self):
        admin_user = _admin()
        device = _com_totp(admin_user)
        c = Client()
        c.force_login(admin_user)
        # sem desafio: o middleware manda p/ o challenge
        resp = c.get('/django-admin-sv/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('aranha:admin_2fa_challenge'), resp['Location'])
        resp = c.post(reverse('aranha:admin_2fa_verify'), {
            'token': _token(device), 'next': '/django-admin-sv/',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/django-admin-sv/')
        self.assertEqual(c.get('/django-admin-sv/').status_code, 200)

    def test_staff_sem_totp_no_admin_vai_para_cadastro(self):
        admin_user = _admin()
        c = Client()
        c.force_login(admin_user)
        resp = c.get('/django-admin-sv/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('aranha:admin_2fa_setup'), resp['Location'])

    def test_verify_com_codigo_errado_nao_libera(self):
        admin_user = _admin()
        _com_totp(admin_user)
        c = Client()
        c.force_login(admin_user)
        c.post(reverse('aranha:admin_2fa_verify'), {'token': '000000'})
        self.assertEqual(c.get(reverse('aranha:painel_overview')).status_code, 302)


# ─── security-13: /api/ coberta + 2FA obrigatorio p/ ADMIN ───

class Enforce2FAApiEObrigatorioTests(TestCase):
    def test_api_exige_2fa_quando_ha_device(self):
        admin_user = _admin()
        _com_totp(admin_user)
        c = Client()
        c.force_login(admin_user)
        resp = c.get('/api/v1/clientes/')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {'detail': '2fa_required'})

    @override_settings(ADMIN_2FA_OBRIGATORIO=True)
    def test_login_admin_sem_totp_vai_para_cadastro_obrigatorio(self):
        _admin()
        c = Client()
        resp = c.post(reverse('aranha:usuario_login'), {'username': 'adm@test.com', 'password': SENHA})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('aranha:admin_2fa_setup'), resp['Location'])
        # preso no cadastro: painel e API barrados
        resp = c.get(reverse('aranha:painel_overview'))
        self.assertIn(reverse('aranha:admin_2fa_setup'), resp['Location'])
        self.assertEqual(c.get('/api/v1/clientes/').status_code, 403)
        # cadastra e confirma -> liberado
        c.post(reverse('aranha:admin_2fa_setup'), {'acao': 'gerar'})
        pendente = TOTPDevice.objects.get(user__email='adm@test.com', confirmed=False)
        c.post(reverse('aranha:admin_2fa_setup'), {'acao': 'confirmar', 'token': _token(pendente)})
        self.assertEqual(c.get(reverse('aranha:painel_overview')).status_code, 200)

    @override_settings(ADMIN_2FA_OBRIGATORIO=False)
    def test_login_admin_sem_obrigatoriedade_vai_ao_painel(self):
        _admin()
        c = Client()
        resp = c.post(reverse('aranha:usuario_login'), {'username': 'adm@test.com', 'password': SENHA})
        self.assertEqual(resp['Location'], reverse('aranha:painel_overview'))


# ─── security-09 / crawl-08 / admin_views-07: login por papel ───

@override_settings(ADMIN_2FA_OBRIGATORIO=False)
class LoginPorPapelTests(TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('aranha:usuario_login')

    def _login(self, email, **extra):
        c = Client()
        resp = c.post(self.url, {'username': email, 'password': SENHA, **extra})
        return c, resp

    def test_profissional_ativo_loga_e_cai_no_portal(self):
        _profissional_user()
        c, resp = self._login('prof@test.com')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('aranha:profissional_agenda'))
        self.assertEqual(c.get(reverse('aranha:profissional_agenda')).status_code, 200)

    def test_profissional_next_do_painel_e_ignorado(self):
        _profissional_user()
        _, resp = self._login('prof@test.com', next='/painel/overview/')
        self.assertEqual(resp['Location'], reverse('aranha:profissional_agenda'))

    def test_profissional_next_do_portal_e_respeitado(self):
        _profissional_user()
        _, resp = self._login('prof@test.com', next='/profissional/?data=2026-10-01')
        self.assertEqual(resp['Location'], '/profissional/?data=2026-10-01')

    def test_next_externo_descartado(self):
        _admin()
        _, resp = self._login('adm@test.com', next='https://evil.example.com/')
        self.assertEqual(resp['Location'], reverse('aranha:painel_overview'))

    def test_profissional_inativo_nao_loga(self):
        _profissional_user(ativo_prof=False)
        c, resp = self._login('prof@test.com')
        self.assertEqual(resp['Location'], self.url)
        self.assertNotIn('_auth_user_id', c.session)

    def test_recepcao_nao_loga(self):
        Usuario.objects.create_user(email='recep@test.com', password=SENHA, nome='Recep')
        c, resp = self._login('recep@test.com')
        self.assertEqual(resp['Location'], self.url)
        self.assertNotIn('_auth_user_id', c.session)

    def test_profissional_no_painel_volta_para_o_portal(self):
        user, _ = _profissional_user()
        c = Client()
        c.force_login(user)
        resp = c.get(reverse('aranha:painel_overview'))
        self.assertEqual(resp['Location'], reverse('aranha:profissional_agenda'))

    def test_ja_logado_segue_para_area_do_papel(self):
        user, _ = _profissional_user()
        c = Client()
        c.force_login(user)
        resp = c.get(self.url)
        self.assertEqual(resp['Location'], reverse('aranha:profissional_agenda'))

    def test_link_do_email_ao_profissional_nao_da_405(self):
        html = render_to_string('email/aprovacao_profissional.html', {
            'site_url': 'https://clinica.example.com',
            'dados': {'profissional': 'Ana', 'cliente': 'Maria', 'procedimento': 'Drenagem',
                      'data_hora': '01/10/2026 10:00'},
        })
        self.assertIn('https://clinica.example.com/profissional/', html)
        self.assertNotIn('/aprovar/', html)
        self.assertNotIn('/rejeitar/', html)


# ─── security-05 / public_front-20: reset de senha ───

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ResetSenhaTests(TestCase):
    def setUp(self):
        super().setUp()
        _admin(nome="Ana D'Ávila")
        self.url = reverse('aranha:password_reset')

    def test_email_existente_envia_link_com_nome_da_clinica(self):
        resp = Client().post(self.url, {'email': 'ADM@test.com'})
        self.assertRedirects(resp, reverse('aranha:password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertNotIn('﻿', msg.subject)
        self.assertNotIn('﻿', msg.body)
        from aranha_estetica.utils.branding import get_branding
        self.assertTrue(msg.subject.startswith(get_branding()['CLINIC_NAME']), msg.subject)
        self.assertIn('Recuperação de senha', msg.subject)
        self.assertRegex(msg.body, r'https?://\S+/admin-login/recuperar/\S+/\S+/')
        self.assertIn("Ana D'Ávila", msg.body)

    def test_email_inexistente_nao_quebra_nem_envia(self):
        resp = Client().post(self.url, {'email': 'naoexiste@test.com'})
        self.assertRedirects(resp, reverse('aranha:password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def test_usuario_inativo_nao_recebe(self):
        Usuario.objects.filter(email='adm@test.com').update(ativo=False)
        Client().post(self.url, {'email': 'adm@test.com'})
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend', DEBUG=False)
    def test_sem_provedor_falha_fechado(self):
        resp = Client().post(self.url, {'email': 'adm@test.com'})
        self.assertRedirects(resp, reverse('aranha:password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def test_filebased_nao_conta_como_entrega(self):
        # gap4-06: o link de senha iria p/ o disco efemero do container
        with tempfile.TemporaryDirectory() as pasta, override_settings(
            DEBUG=False, EMAIL_BACKEND='django.core.mail.backends.filebased.EmailBackend',
            EMAIL_FILE_PATH=pasta,
        ):
            resp = Client().post(self.url, {'email': 'adm@test.com'})
            self.assertRedirects(resp, reverse('aranha:password_reset_done'))
            self.assertEqual(list(Path(pasta).iterdir()), [])

    def test_um_so_contrato_de_email_configurado(self):
        from importlib import import_module
        from aranha_estetica.utils import email as email_utils
        # views/__init__ reexporta as funcoes com o mesmo nome dos modulos
        for modulo in ('auth', 'admin_usuarios'):
            with self.subTest(modulo=modulo):
                mod = import_module(f'aranha_estetica.views.{modulo}')
                self.assertIs(mod.email_configurado, email_utils.email_configurado)


# ─── logout so via POST ───

class LogoutTests(TestCase):
    def test_get_mostra_confirmacao_sem_deslogar(self):
        user, _ = _profissional_user()
        c = Client()
        c.force_login(user)
        resp = c.get(reverse('aranha:usuario_logout'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('_auth_user_id', c.session)

    def test_post_desloga(self):
        user, _ = _profissional_user()
        c = Client()
        c.force_login(user)
        resp = c.post(reverse('aranha:usuario_logout'))
        self.assertRedirects(resp, reverse('aranha:inicio'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', c.session)


# ─── admin_templates-01/06/29, crawl-05, pgtests-06: telas de usuario ───

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class UsuarioFormTests(TestCase):
    def setUp(self):
        super().setUp()
        self.admin = _admin()
        self.c = Client()
        self.c.force_login(self.admin)
        self.outro, self.prof = _profissional_user()

    def test_get_criar_e_editar_nao_dao_500(self):
        self.assertEqual(self.c.get(reverse('aranha:admin_criar_usuario')).status_code, 200)
        resp = self.c.get(reverse('aranha:admin_editar_usuario', args=[self.outro.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="prof@test.com"')

    def test_editar_sem_form_aninhado_e_reset_com_action_propria(self):
        resp = self.c.get(reverse('aranha:admin_editar_usuario', args=[self.outro.pk]))
        parser = _FormAninhado()
        parser.feed(resp.content.decode())
        self.assertFalse(parser.aninhado, 'form de reset aninhado no form de edicao')
        self.assertContains(resp, reverse('aranha:admin_resetar_senha_usuario', args=[self.outro.pk]))

    def test_criar_oculta_recepcao(self):
        resp = self.c.get(reverse('aranha:admin_criar_usuario'))
        self.assertNotContains(resp, 'value="RECEPCAO"')

    def test_editar_email_duplicado_mostra_erro(self):
        resp = self.c.post(reverse('aranha:admin_editar_usuario', args=[self.outro.pk]), {
            'nome': 'X', 'email': 'adm@test.com', 'papel': 'PROFISSIONAL',
            'profissional_id': self.prof.pk, 'ativo': '1',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'E-mail já em uso')

    def test_admin_nao_rebaixa_nem_desativa_a_si_mesmo(self):
        url = reverse('aranha:admin_editar_usuario', args=[self.admin.pk])
        self.c.post(url, {'nome': 'Admin', 'email': 'adm@test.com', 'papel': 'PROFISSIONAL',
                          'profissional_id': '', 'ativo': '1'})
        self.c.post(url, {'nome': 'Admin', 'email': 'adm@test.com', 'papel': 'ADMIN'})
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.papel, Usuario.PAPEL_ADMIN)
        self.assertTrue(self.admin.ativo)

    def test_criar_profissional_exige_vinculo_e_envia_link(self):
        prof2 = Profissional.objects.create(nome='Dra. Nova', ativo=True)
        url = reverse('aranha:admin_criar_usuario')
        resp = self.c.post(url, {'nome': 'Nova', 'email': 'nova@test.com', 'papel': 'PROFISSIONAL'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Usuario.objects.filter(email='nova@test.com').exists())
        resp = self.c.post(url, {'nome': 'Nova', 'email': 'nova@test.com', 'papel': 'PROFISSIONAL',
                                 'profissional_id': prof2.pk})
        self.assertRedirects(resp, reverse('aranha:admin_usuarios'), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/admin-login/recuperar/', mail.outbox[0].body)

    def test_vinculo_duplicado_nao_da_500(self):
        resp = self.c.post(reverse('aranha:admin_criar_usuario'), {
            'nome': 'Dup', 'email': 'dup@test.com', 'papel': 'PROFISSIONAL',
            'profissional_id': self.prof.pk,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'já está vinculado')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend', DEBUG=False)
    def test_sem_email_configurado_exige_senha_inicial(self):
        prof2 = Profissional.objects.create(nome='Dra. Sem Email', ativo=True)
        resp = self.c.post(reverse('aranha:admin_criar_usuario'), {
            'nome': 'Sem', 'email': 'sem@test.com', 'papel': 'PROFISSIONAL',
            'profissional_id': prof2.pk,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Usuario.objects.filter(email='sem@test.com').exists())


# ─── admin_views-21 / admin_views-31: Django admin ───

class DjangoAdminTests(TestCase):
    def setUp(self):
        super().setUp()
        self.admin = _admin()
        device = _com_totp(self.admin)
        self.c = Client()
        self.c.force_login(self.admin)
        self.c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(device)})

    def test_criar_usuario_grava_hash(self):
        resp = self.c.post('/django-admin-sv/aranha_estetica/usuario/add/', {
            'email': 'novo@test.com', 'nome': 'Novo', 'papel': 'ADMIN',
            'password1': SENHA, 'password2': SENHA,
        })
        self.assertEqual(resp.status_code, 302, getattr(resp, 'context', None) and resp.context.get('errors'))
        novo = Usuario.objects.get(email='novo@test.com')
        self.assertNotEqual(novo.password, SENHA)
        self.assertTrue(novo.check_password(SENHA))

    def test_telas_do_usuario_no_admin_abrem(self):
        self.assertEqual(self.c.get('/django-admin-sv/aranha_estetica/usuario/').status_code, 200)
        url = f'/django-admin-sv/aranha_estetica/usuario/{self.admin.pk}/change/'
        resp = self.c.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, self.admin.password)

    def test_cliente_sem_delete_em_massa_e_log_nao_apagavel(self):
        rf = RequestFactory().get('/')
        rf.user = self.admin
        cliente_admin = django_admin.site._registry[Cliente]
        self.assertNotIn('delete_selected', cliente_admin.get_actions(rf))
        log_admin = django_admin.site._registry[LogAuditoria]
        self.assertFalse(log_admin.has_delete_permission(rf))

    def test_acao_soft_delete_de_cliente(self):
        cli = Cliente.objects.create(nome='Soft', telefone='17999990000')
        cliente_admin = django_admin.site._registry[Cliente]
        rf = RequestFactory().post('/')
        rf.user = self.admin
        rf._messages = type('M', (), {'add': lambda *a, **k: None})()
        cliente_admin.acao_excluir_soft(rf, Cliente.all_objects.filter(pk=cli.pk))
        self.assertTrue(Cliente.all_objects.filter(pk=cli.pk, ativo=False).exists())

    def test_troca_de_senha_do_admin_abre(self):
        url = f'/django-admin-sv/aranha_estetica/usuario/{self.admin.pk}/password/'
        self.assertEqual(self.c.get(url).status_code, 200)

    def test_actions_de_atendimento_usam_fsm(self):
        from aranha_estetica.models import Atendimento
        from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        ok = criar_atendimento(criar_cliente(), prof, proc, status='AGENDADO')
        cancelado = criar_atendimento(
            criar_cliente(), prof, proc, status='CANCELADO',
            data_hora=ok.data_hora_inicio + timedelta(hours=2),
        )
        resp = self.c.post('/django-admin-sv/aranha_estetica/atendimento/', {
            'action': 'acao_marcar_realizado', '_selected_action': [ok.pk, cancelado.pk],
        })
        self.assertEqual(resp.status_code, 302)
        ok.refresh_from_db()
        cancelado.refresh_from_db()
        self.assertEqual(ok.status, Atendimento.STATUS_REALIZADO)
        self.assertEqual(cancelado.status, Atendimento.STATUS_CANCELADO)
        self.assertTrue(LogAuditoria.objects.filter(tabela='atendimento', registro_id=ok.pk).exists())

    def test_regra_comissao_e_feriado_registrados(self):
        from aranha_estetica.models import Feriado, RegraComissao
        self.assertIn(RegraComissao, django_admin.site._registry)
        self.assertIn(Feriado, django_admin.site._registry)


class SwaggerTests(TestCase):
    def test_swagger_sem_script_inline(self):
        admin_user = _admin()
        device = _com_totp(admin_user)
        c = Client()
        c.force_login(admin_user)
        c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(device)})
        resp = c.get('/api/schema/swagger/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIsNone(re.search(r'<script>(?!\s*</script>)', html), 'script inline sem nonce')
        self.assertIn('?script', html)
        # swagger-ui vem do CDN: a CSP (restrita por caminho) precisa liberar
        from .test_csp import _diretiva, permitido_pela_csp
        csp = resp.headers['Content-Security-Policy']
        for tag, url in re.findall(r'<(script|link)\b[^>]*?(?:src|href)="(https://[^"]+)"', html):
            if tag == 'link' and url.endswith('.png'):
                continue  # favicon: img-src https:
            diretiva = _diretiva(csp, 'script-src' if tag == 'script' else 'style-src')
            with self.subTest(url=url):
                self.assertTrue(permitido_pela_csp(url, diretiva), f'{url} bloqueada pela CSP')


# ─── Telas de auth/2FA renderizam (CSP-safe, sem 500) ───

@override_settings(ADMIN_2FA_OBRIGATORIO=False)
class TelasAuthRenderTests(TestCase):
    def test_login_preserva_next_no_post(self):
        resp = Client().get(reverse('aranha:usuario_login') + '?next=/profissional/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="next" value="/profissional/"')

    def test_telas_de_reset_abrem(self):
        c = Client()
        for nome in ('password_reset', 'password_reset_done', 'password_reset_complete'):
            with self.subTest(tela=nome):
                self.assertEqual(c.get(reverse(f'aranha:{nome}')).status_code, 200)
        html = c.get(reverse('aranha:password_reset_complete')).content.decode()
        self.assertIsNone(re.search(r'<button[^>]*>\s*<a ', html), '<a> dentro de <button>')

    def test_setup_2fa_fluxo_de_telas(self):
        admin_user = _admin()
        c = Client()
        c.force_login(admin_user)
        url = reverse('aranha:admin_2fa_setup')
        resp = c.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Ativar 2FA agora')
        resp = c.post(url, {'acao': 'gerar'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data:image/svg+xml;base64,')
        self.assertEqual(c.get(url).status_code, 200)
        # "Gerar novo QR" nao pode exigir o codigo (validacao HTML5 do input required)
        self.assertRegex(resp.content.decode(), r'value="gerar"\s+formnovalidate')
        pendente = TOTPDevice.objects.get(user=admin_user, confirmed=False)
        resp = c.post(url, {'acao': 'confirmar', 'token': _token(pendente)}, follow=True)
        self.assertContains(resp, 'name="token"')  # desativar exige o codigo atual
        self.assertContains(resp, 'Desativar 2FA')

    def test_challenge_abre_para_profissional(self):
        user, _ = _profissional_user()
        _com_totp(user)
        c = Client()
        c.force_login(user)
        resp = c.get(reverse('aranha:profissional_agenda'))
        self.assertEqual(resp.status_code, 302)
        resp = c.get(resp['Location'])
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Verificação em duas etapas')

    def test_setup_abre_para_profissional(self):
        user, _ = _profissional_user()
        c = Client()
        c.force_login(user)
        self.assertEqual(c.get(reverse('aranha:admin_2fa_setup')).status_code, 200)


# ─── gap4-01: QR do cadastro de 2FA sem Pillow (imagem de producao) ───

@override_settings(ADMIN_2FA_OBRIGATORIO=True)
class Setup2FASemPillowTests(TestCase):
    def test_admin_obrigatorio_cadastra_2fa_sem_pillow(self):
        _admin()
        c = Client()
        url = reverse('aranha:admin_2fa_setup')
        with _sem_pillow():
            resp = c.post(reverse('aranha:usuario_login'), {'username': 'adm@test.com', 'password': SENHA})
            self.assertIn(url, resp['Location'])
            c.post(url, {'acao': 'gerar'})
            resp = c.get(url)
            self.assertEqual(resp.status_code, 200)
            svg = _qr_svg(resp.content.decode())
            self.assertIn('<svg', svg)
            self.assertIn('fill="white"', svg)  # fundo claro p/ o leitor do app
            pendente = TOTPDevice.objects.get(user__email='adm@test.com', confirmed=False)
            c.post(url, {'acao': 'confirmar', 'token': _token(pendente)})
            self.assertEqual(c.get(reverse('aranha:painel_overview')).status_code, 200)


# ─── gap1-02 / gap2-07: prova de consentimento e dado clinico no Django admin ───

class DjangoAdminEvidenciasTests(TestCase):
    def setUp(self):
        super().setUp()
        from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional
        self.admin = _admin()
        device = _com_totp(self.admin)
        self.c = Client()
        self.c.force_login(self.admin)
        self.c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(device)})
        prof = criar_profissional()
        self.proc = criar_procedimento(profissional=prof)
        self.cliente = criar_cliente(telefone='17999990001')
        self.atd = criar_atendimento(self.cliente, prof, self.proc)
        self.versao = VersaoTermo.objects.create(
            tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo Limpeza',
            conteudo='Texto aceito pela cliente.', versao='1.0', vigente_desde=date(2026, 1, 1),
        )

    def _url(self, modelo, acao, pk=None):
        base = f'/django-admin-sv/aranha_estetica/{modelo}/'
        return f'{base}{pk}/{acao}/' if pk else f'{base}{acao}/'

    def test_anotacao_de_sessao_so_leitura_e_leitura_auditada(self):
        anot = AnotacaoSessao.objects.create(atendimento=self.atd, autor=self.admin, texto='Pele sensível.')
        self.assertEqual(self.c.get(self._url('anotacaosessao', 'add')).status_code, 403)
        resp = self.c.post(self._url('anotacaosessao', 'change', anot.pk), {
            'atendimento': self.atd.pk, 'autor': self.admin.pk, 'texto': 'reescrito',
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.c.post(self._url('anotacaosessao', 'delete', anot.pk), {'post': 'yes'}).status_code, 403)
        anot.refresh_from_db()
        self.assertEqual(anot.texto, 'Pele sensível.')
        resp = self.c.get(self._url('anotacaosessao', 'change', anot.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'name="texto"')
        self.assertTrue(LogAuditoria.objects.filter(
            tabela='anotacao_sessao', registro_id=anot.pk, acao__icontains='django-admin',
        ).exists())

    def test_prontuario_so_leitura_e_leitura_auditada(self):
        pront = Prontuario.objects.create(cliente=self.cliente, alergias='Dipirona')
        resp = self.c.post(self._url('prontuario', 'change', pront.pk), {
            'cliente': self.cliente.pk, 'alergias': 'nenhuma', 'respostas_extras': '{}',
        })
        self.assertEqual(resp.status_code, 403)
        pront.refresh_from_db()
        self.assertEqual(pront.alergias, 'Dipirona')
        self.assertEqual(self.c.get(self._url('prontuario', 'change', pront.pk)).status_code, 200)
        self.assertTrue(LogAuditoria.objects.filter(
            tabela='prontuario', registro_id=self.cliente.pk,
            acao='Visualizou prontuario (django-admin)',
        ).exists())

    def test_aceite_de_termo_nao_e_criado_alterado_nem_apagado(self):
        aceite = AceiteTermo.objects.create(cliente=self.cliente, versao_termo=self.versao, ip='10.0.0.1')
        self.assertEqual(self.c.get(self._url('aceitetermo', 'add')).status_code, 403)
        resp = self.c.post(self._url('aceitetermo', 'change', aceite.pk), {
            'cliente': self.cliente.pk, 'versao_termo': self.versao.pk, 'ip': '1.2.3.4',
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.c.post(self._url('aceitetermo', 'delete', aceite.pk), {'post': 'yes'}).status_code, 403)
        aceite.refresh_from_db()
        self.assertEqual(aceite.ip, '10.0.0.1')
        self.assertEqual(self.c.get(self._url('aceitetermo', 'change', aceite.pk)).status_code, 200)

    def test_versao_com_aceite_congela_texto_mas_pode_desativar(self):
        AceiteTermo.objects.create(cliente=self.cliente, versao_termo=self.versao)
        url = self._url('versaotermo', 'change', self.versao.pk)
        resp = self.c.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'name="conteudo"')
        self.assertContains(resp, 'name="ativa"')
        # tentativa de adulterar o texto: campos so-leitura sao ignorados; so desativa
        resp = self.c.post(url, {'titulo': 'X', 'conteudo': 'TEXTO ADULTERADO', 'versao': '9'})
        self.assertEqual(resp.status_code, 302)
        self.versao.refresh_from_db()
        self.assertEqual(self.versao.conteudo, 'Texto aceito pela cliente.')
        self.assertEqual(self.versao.versao, '1.0')
        self.assertFalse(self.versao.ativa)
        self.assertEqual(self.c.post(self._url('versaotermo', 'delete', self.versao.pk), {'post': 'yes'}).status_code, 403)

    def test_versao_sem_aceite_segue_editavel(self):
        resp = self.c.get(self._url('versaotermo', 'change', self.versao.pk))
        self.assertContains(resp, 'name="conteudo"')

    def test_reativar_versao_com_outra_ativa_mostra_erro_sem_500(self):
        AceiteTermo.objects.create(cliente=self.cliente, versao_termo=self.versao)
        VersaoTermo.objects.filter(pk=self.versao.pk).update(ativa=False)
        VersaoTermo.objects.create(
            tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo Limpeza', conteudo='v2',
            versao='2.0', vigente_desde=date(2026, 6, 1),
        )
        resp = self.c.post(self._url('versaotermo', 'change', self.versao.pk), {'ativa': 'on'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Já existe uma versão ativa')

    def test_cliente_consentimento_evidencia_so_leitura_e_nao_concedido_pelo_admin(self):
        from django.forms.models import model_to_dict
        cliente_admin = django_admin.site._registry[Cliente]
        rf = RequestFactory().get('/')
        rf.user = self.admin
        readonly = cliente_admin.get_readonly_fields(rf, self.cliente)
        for campo in ('email_marketing', 'whatsapp_nps', 'whatsapp_confirmacao'):
            with self.subTest(campo=campo):
                self.assertIn(f'consent_{campo}_em', readonly)
                self.assertIn(f'consent_{campo}_ip', readonly)
        form_cls = cliente_admin.get_form(rf, self.cliente)
        self.assertIn('consent_whatsapp_confirmacao', form_cls.base_fields)
        dados = {k: v for k, v in model_to_dict(self.cliente, fields=form_cls.base_fields).items() if v is not None}
        dados['consent_email_marketing'] = True
        form = form_cls(data=dados, instance=self.cliente)
        self.assertFalse(form.is_valid())
        self.assertIn('consent_email_marketing', form.errors)

    def test_busca_do_admin_acha_telefone_e_cpf_com_mascara(self):
        cliente_admin = django_admin.site._registry[Cliente]
        rf = RequestFactory().get('/')
        rf.user = self.admin
        qs = cliente_admin.get_queryset(rf)
        achados, _ = cliente_admin.get_search_results(rf, qs, '(17) 99999-0001')
        self.assertIn(self.cliente, achados)
