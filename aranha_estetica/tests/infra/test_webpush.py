"""Web Push (services/push): o codigo HTTP do envio roda de verdade (so o pywebpush e falso), o
contato VAPID vem da clinica e a assinatura so morre quando o push service diz que acabou."""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from pywebpush import WebPushException

from aranha_estetica.models import AssinaturaPush, Usuario
from aranha_estetica.services import push

WEBPUSH = 'pywebpush.webpush'
PAYLOAD = {'head': 'Novo pedido', 'body': 'Ana S. - Limpeza de Pele', 'url': '/painel/'}


def _falha_http(status):
    return WebPushException('falhou', response=mock.Mock(status_code=status))


@override_settings(WEBPUSH_VAPID_PRIVATE_KEY='chave-privada', WEBPUSH_VAPID_CLAIMS_EMAIL='',
                   ADMIN_EMAIL='', MARCA_ENV={})
class EnvioPushTests(TestCase):
    def setUp(self):
        cache.clear()  # get_branding guarda a marca em cache
        self.user = Usuario.objects.create_user(email='dona@clinica.test', password='x-senha-123', nome='Dona')
        self.sub = AssinaturaPush.objects.create(user=self.user, endpoint='https://push.example.com/a',
                                                 p256dh='chave', auth='segredo')

    @override_settings(WEBPUSH_VAPID_CLAIMS_EMAIL='contato@clinica.test')
    def test_entrega_com_o_contato_da_clinica(self):
        with mock.patch(WEBPUSH) as webpush:
            self.assertTrue(push.send_push(self.sub, PAYLOAD))
        kwargs = webpush.call_args.kwargs
        self.assertEqual(kwargs['vapid_claims'], {'sub': 'mailto:contato@clinica.test'})
        self.assertEqual(kwargs['vapid_private_key'], 'chave-privada')
        self.assertEqual(kwargs['subscription_info']['endpoint'], 'https://push.example.com/a')

    def test_contato_cai_no_admin_e_depois_no_email_da_marca(self):
        with override_settings(ADMIN_EMAIL='admin@clinica.test'):
            self.assertEqual(push._claims(), {'sub': 'mailto:admin@clinica.test'})
        with override_settings(MARCA_ENV={'CLINIC_EMAIL': 'oi@clinica.test'}):
            cache.clear()
            self.assertEqual(push._claims(), {'sub': 'mailto:oi@clinica.test'})

    def test_sem_contato_nao_envia(self):
        with mock.patch(WEBPUSH) as webpush, self.assertLogs('aranha_estetica.services.push', 'WARNING') as logs:
            self.assertFalse(push.send_push(self.sub, PAYLOAD))
        webpush.assert_not_called()
        self.assertIn('webpush_sem_contato', logs.output[0])

    @override_settings(WEBPUSH_VAPID_PRIVATE_KEY='', WEBPUSH_VAPID_CLAIMS_EMAIL='contato@clinica.test')
    def test_sem_chave_nao_chama_o_push_service(self):
        with mock.patch(WEBPUSH) as webpush:
            self.assertFalse(push.send_push(self.sub, PAYLOAD))
        webpush.assert_not_called()

    @override_settings(WEBPUSH_VAPID_CLAIMS_EMAIL='contato@clinica.test')
    def test_410_desativa_a_assinatura_e_500_mantem(self):
        with mock.patch(WEBPUSH, side_effect=_falha_http(500)):
            self.assertFalse(push.send_push(self.sub, PAYLOAD))
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.ativo)
        self.assertIsNone(self.sub.ultima_falha_em)

        with mock.patch(WEBPUSH, side_effect=_falha_http(410)):
            self.assertFalse(push.send_push(self.sub, PAYLOAD))
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.ativo)
        self.assertIsNotNone(self.sub.ultima_falha_em)

    @override_settings(WEBPUSH_VAPID_CLAIMS_EMAIL='contato@clinica.test')
    def test_envio_ao_usuario_conta_so_os_sucessos_e_ignora_inativas(self):
        AssinaturaPush.objects.create(user=self.user, endpoint='https://push.example.com/b',
                                      p256dh='chave', auth='segredo')
        AssinaturaPush.objects.create(user=self.user, endpoint='https://push.example.com/c',
                                      p256dh='chave', auth='segredo', ativo=False)
        falhas = [None, _falha_http(500)]
        with mock.patch(WEBPUSH, side_effect=falhas) as webpush:
            self.assertEqual(push.send_push_to_user(self.user, PAYLOAD), 1)
        self.assertEqual(webpush.call_count, 2)


class EndpointDePushTests(TestCase):
    """O servidor faz POST no endpoint da assinatura: so push services conhecidos (antes bastava
    ser https, e um usuario da equipe apontava o envio para qualquer host)."""

    def test_push_services_dos_navegadores(self):
        for url in ('https://fcm.googleapis.com/fcm/send/abc',
                    'https://updates.push.services.mozilla.com/wpush/v2/abc',
                    'https://web.push.apple.com/QGx',
                    'https://wns2-par02p.notify.windows.com/w/?token=abc'):
            with self.subTest(url=url):
                self.assertTrue(push.endpoint_de_push_valido(url))

    def test_recusa_host_desconhecido_porta_credencial_e_http(self):
        for url in ('https://atacante.example/push', 'https://fcm.googleapis.com.atacante.example/x',
                    'https://fcm.googleapis.com:8443/x', 'https://user:pw@fcm.googleapis.com/x',
                    'http://fcm.googleapis.com/x', 'https://[::1/x', '',
                    'https://fcm.googleapis.com/' + 'a' * 600):
            with self.subTest(url=url[:60]):
                self.assertFalse(push.endpoint_de_push_valido(url))


@override_settings(ADMIN_2FA_OBRIGATORIO=False, PROFISSIONAL_2FA_OBRIGATORIO=False)
class AssinarPushHostDesconhecidoTests(TestCase):
    def test_endpoint_https_de_host_arbitrario_devolve_400(self):
        import json

        user = Usuario.objects.create_user(email='dona@clinica.test', password='x-senha-123', nome='Dona',
                                           papel=Usuario.PAPEL_ADMIN)
        self.client.force_login(user)
        corpo = json.dumps({'endpoint': 'https://atacante.example/push', 'keys': {'p256dh': 'k', 'auth': 'a'}})
        resp = self.client.post('/webpush/subscribe/', data=corpo, content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(AssinaturaPush.objects.exists())
