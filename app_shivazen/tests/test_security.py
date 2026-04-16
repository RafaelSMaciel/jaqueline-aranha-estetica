"""Testes de seguranca: autorizacao, rate limiting e controle de acesso."""
from django.test import Client, TestCase
from django.urls import reverse

from app_shivazen.models import Usuario

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_procedimento,
    criar_profissional,
)


def _criar_staff(username='admin_test', password='senha123'):
    u = Usuario.objects.create_user(username=username, password=password)
    u.is_staff = True
    u.save()
    return u


def _criar_usuario_comum(username='user_test', password='senha123'):
    return Usuario.objects.create_user(username=username, password=password)


class StaffRequiredTests(TestCase):
    """Garante que endpoints admin bloqueiam usuarios nao-staff."""

    ENDPOINTS_STAFF = [
        ('shivazen:painel', {}),
        ('shivazen:painel_overview', {}),
        ('shivazen:painel_agendamentos', {}),
        ('shivazen:painel_clientes', {}),
        ('shivazen:painel_profissionais', {}),
        ('shivazen:admin_promocoes', {}),
        ('shivazen:admin_procedimentos', {}),
        ('shivazen:admin_auditoria', {}),
    ]

    def setUp(self):
        self.client = Client()

    def test_anonimo_redirecionado_para_login(self):
        for name, kwargs in self.ENDPOINTS_STAFF:
            with self.subTest(endpoint=name):
                url = reverse(name, kwargs=kwargs)
                resp = self.client.get(url)
                self.assertIn(resp.status_code, [302, 403],
                    f'{name} deveria redirecionar anonimo')

    def test_usuario_comum_bloqueado(self):
        user = _criar_usuario_comum()
        self.client.force_login(user)
        for name, kwargs in self.ENDPOINTS_STAFF:
            with self.subTest(endpoint=name):
                url = reverse(name, kwargs=kwargs)
                resp = self.client.get(url)
                self.assertIn(resp.status_code, [302, 403],
                    f'{name} deveria bloquear usuario comum')

    def test_staff_permite_acesso(self):
        staff = _criar_staff()
        self.client.force_login(staff)
        for name, kwargs in self.ENDPOINTS_STAFF:
            with self.subTest(endpoint=name):
                url = reverse(name, kwargs=kwargs)
                resp = self.client.get(url)
                self.assertNotEqual(resp.status_code, 403,
                    f'{name} deveria permitir staff')


class ProntuarioAcessoTests(TestCase):
    """Prontuario exige staff — usuario comum nao acessa."""

    def setUp(self):
        self.client = Client()
        self.cliente = criar_cliente()

    def test_anonimo_bloqueado(self):
        url = reverse('shivazen:prontuario_detalhe', kwargs={'cliente_id': self.cliente.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_usuario_comum_bloqueado(self):
        user = _criar_usuario_comum()
        self.client.force_login(user)
        url = reverse('shivazen:prontuario_detalhe', kwargs={'cliente_id': self.cliente.pk})
        resp = self.client.get(url)
        self.assertIn(resp.status_code, [302, 403])

    def test_staff_acessa(self):
        staff = _criar_staff()
        self.client.force_login(staff)
        url = reverse('shivazen:prontuario_detalhe', kwargs={'cliente_id': self.cliente.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_cliente_inexistente_retorna_404(self):
        staff = _criar_staff()
        self.client.force_login(staff)
        url = reverse('shivazen:prontuario_detalhe', kwargs={'cliente_id': 999999})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)


class AdminAtualizarStatusTests(TestCase):
    """Endpoint AJAX de status exige staff e metodo POST."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('shivazen:admin_atualizar_status')
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()
        self.atd = criar_atendimento(self.cli, self.prof, self.proc)

    def test_anonimo_bloqueado(self):
        import json
        resp = self.client.post(
            self.url,
            data=json.dumps({'atendimento_id': self.atd.pk, 'status': 'CONFIRMADO'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 302)

    def test_usuario_comum_bloqueado(self):
        import json
        user = _criar_usuario_comum()
        self.client.force_login(user)
        resp = self.client.post(
            self.url,
            data=json.dumps({'atendimento_id': self.atd.pk, 'status': 'CONFIRMADO'}),
            content_type='application/json',
        )
        self.assertIn(resp.status_code, [302, 403])

    def test_staff_atualiza_status(self):
        import json
        staff = _criar_staff()
        self.client.force_login(staff)
        resp = self.client.post(
            self.url,
            data=json.dumps({'atendimento_id': self.atd.pk, 'status': 'CONFIRMADO'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.atd.refresh_from_db()
        self.assertEqual(self.atd.status, 'CONFIRMADO')

    def test_status_invalido_rejeitado(self):
        import json
        staff = _criar_staff()
        self.client.force_login(staff)
        resp = self.client.post(
            self.url,
            data=json.dumps({'atendimento_id': self.atd.pk, 'status': 'INVALIDO'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)


class BuscarHorariosAJAXTests(TestCase):
    """Endpoint AJAX de horarios retorna erro explicito para params invalidos."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('shivazen:buscar_horarios')
        self.prof = criar_profissional()

    def test_sem_params_retorna_400(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 400)

    def test_data_invalida_retorna_400(self):
        resp = self.client.get(self.url, {'profissional_id': self.prof.pk, 'data': 'nao-e-data'})
        self.assertEqual(resp.status_code, 400)

    def test_profissional_inexistente_retorna_404(self):
        resp = self.client.get(self.url, {'profissional_id': 999999, 'data': '2026-06-01'})
        self.assertEqual(resp.status_code, 404)
