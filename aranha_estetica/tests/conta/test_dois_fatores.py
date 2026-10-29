"""2FA da equipe falha fechado: erro de banco na consulta do TOTP nao entrega o painel a uma sessao
que so passou pela senha."""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import TestCase, override_settings
from django.urls import reverse

from aranha_estetica.models import AssinaturaPush
from aranha_estetica.utils.respostas import MSG_2FA

TEM_2FA = 'aranha_estetica.utils.dois_fatores.tem_2fa'
QUEDA = OperationalError('canceling statement due to statement timeout')


@override_settings(ADMIN_2FA_OBRIGATORIO=False)
class Enforce2FAFalhaFechadaTests(TestCase):
    def setUp(self):
        Usuario = get_user_model()
        self.admin = Usuario.objects.create_user(email='dona@clinica.test', password='x-senha-123',
                                                 nome='Dona', papel=Usuario.PAPEL_ADMIN)
        self.client.force_login(self.admin)  # so senha: sessao nao verificada

    def test_erro_na_consulta_do_totp_manda_ao_desafio(self):
        with mock.patch(TEM_2FA, side_effect=QUEDA), self.assertLogs('aranha_estetica.middleware', 'ERROR') as logs:
            resp = self.client.get(reverse('aranha:painel_overview'))
        desafio = reverse('aranha:admin_2fa_challenge')
        self.assertRedirects(resp, f"{desafio}?next={reverse('aranha:painel_overview')}",
                             fetch_redirect_response=False)
        self.assertIn('2fa_indisponivel', logs.output[0])

    def test_sem_erro_e_sem_totp_o_painel_abre(self):
        # controle do teste acima: a queda e o que barra, nao a sessao
        self.assertEqual(self.client.get(reverse('aranha:painel_overview')).status_code, 200)

    def test_rota_json_responde_403_e_nao_assina_push(self):
        corpo = json.dumps({'endpoint': 'https://push.example.com/x', 'keys': {'p256dh': 'k', 'auth': 'a'}})
        with mock.patch(TEM_2FA, side_effect=QUEDA), self.assertLogs('aranha_estetica', 'ERROR'):
            resp = self.client.post(reverse('aranha:webpush_subscribe'), data=corpo,
                                    content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {'ok': False, 'codigo': '2fa_required', 'mensagem': MSG_2FA})
        self.assertFalse(AssinaturaPush.objects.exists())


class FaltaDoisFatoresNoPushTests(TestCase):
    """A view do push confere de novo (defesa em profundidade): o erro de banco conta como 'falta 2FA'."""

    def test_erro_de_banco_conta_como_falta(self):
        from django.test import RequestFactory

        from aranha_estetica.views.infra.webpush import _falta_2fa

        request = RequestFactory().post('/webpush/subscribe/')
        request.session = {}
        request.user = mock.Mock(is_verified=lambda: False)
        with mock.patch(TEM_2FA, side_effect=QUEDA), self.assertLogs('aranha_estetica', 'ERROR'):
            self.assertTrue(_falta_2fa(request))
