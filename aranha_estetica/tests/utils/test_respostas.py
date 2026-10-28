"""Blocos transversais das views: contrato JSON (utils/respostas) e guarda de escrita da equipe
(decorators.escrita_painel / escrita_profissional)."""
import json

from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.base import SessionBase
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django_ratelimit.exceptions import Ratelimited

from aranha_estetica.decorators import LIMITE_ESCRITA_EQUIPE, escrita_painel, escrita_profissional
from aranha_estetica.excecoes import HorarioIndisponivel
from aranha_estetica.models import Usuario
from aranha_estetica.tests.factories import criar_profissional
from aranha_estetica.utils.respostas import json_erro, json_ok, json_recusa


def _corpo(resp):
    return json.loads(resp.content)


class ContratoJsonTests(SimpleTestCase):
    def test_ok(self):
        resp = json_ok(id=7)
        self.assertEqual((resp.status_code, _corpo(resp)), (200, {'ok': True, 'id': 7}))
        self.assertEqual(json_ok(status=201).status_code, 201)

    def test_erro_tem_codigo_estavel_e_mensagem_para_a_pessoa(self):
        resp = json_erro('incorreto', 'Código incorreto.', restantes=2)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(_corpo(resp), {'ok': False, 'codigo': 'incorreto', 'mensagem': 'Código incorreto.',
                                        'restantes': 2})
        self.assertEqual(json_erro('fora', status=503).status_code, 503)

    def test_recusa_de_regra_de_negocio(self):
        resp = json_recusa(HorarioIndisponivel('Esse horário acabou de ser reservado.'))
        self.assertEqual(_corpo(resp), {'ok': False, 'codigo': 'horario_indisponivel',
                                        'mensagem': 'Esse horário acabou de ser reservado.'})


@escrita_painel
def _escrita_painel(request):
    return HttpResponse('feito')


@escrita_profissional
def _escrita_profissional(request):
    return HttpResponse('feito')


class EscritaDaEquipeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.rf = RequestFactory()
        self.admin = Usuario.objects.create_user(email='dona@clinica.test', password='x-senha-123', nome='Dona',
                                                 papel=Usuario.PAPEL_ADMIN)
        self.prof = Usuario.objects.create_user(email='ana@clinica.test', password='x-senha-123', nome='Ana',
                                                papel=Usuario.PAPEL_PROFISSIONAL, profissional=criar_profissional())

    def _req(self, metodo, user):
        request = getattr(self.rf, metodo)('/painel/x/')
        request.user = user
        request.session = SessionBase()
        request._messages = FallbackStorage(request)
        return request

    def test_painel_so_staff_e_so_post(self):
        self.assertEqual(_escrita_painel(self._req('post', self.admin)).status_code, 200)
        self.assertEqual(_escrita_painel(self._req('get', self.admin)).status_code, 405)
        anonimo = _escrita_painel(self._req('post', AnonymousUser()))
        self.assertEqual(anonimo.status_code, 302)
        self.assertIn('/admin-login/', anonimo['Location'])
        self.assertEqual(_escrita_painel(self._req('post', self.prof))['Location'], '/profissional/')

    def test_profissional_so_post(self):
        self.assertEqual(_escrita_profissional(self._req('post', self.prof)).status_code, 200)
        self.assertEqual(_escrita_profissional(self._req('get', self.prof)).status_code, 405)

    @override_settings(RATELIMIT_ENABLE=True)
    def test_rajada_de_escritas_e_barrada(self):
        limite = int(LIMITE_ESCRITA_EQUIPE.split('/')[0])
        for _ in range(limite):
            self.assertEqual(_escrita_painel(self._req('post', self.admin)).status_code, 200)
        with self.assertRaises(Ratelimited):  # o RatelimitMiddleware transforma em 429
            _escrita_painel(self._req('post', self.admin))
