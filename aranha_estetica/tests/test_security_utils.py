"""Testes do modulo utils.security (PII masking, comparison, IP do cliente, next)."""
from django.test import RequestFactory, SimpleTestCase, override_settings

from aranha_estetica.utils.security import (
    client_ip,
    mask_cpf,
    mask_email,
    mask_telefone,
    safe_next,
    safe_str_compare,
)


class MaskingTests(SimpleTestCase):
    def test_mask_email(self):
        masked = mask_email('rafael@example.com')
        self.assertTrue(masked.startswith('raf'))
        self.assertIn('@example.com', masked)
        self.assertEqual(mask_email(''), '')
        self.assertEqual(mask_email(None), '')

    def test_mask_cpf(self):
        masked = mask_cpf('52998224725')
        self.assertIn('***', masked)
        self.assertTrue(masked.endswith('25'))

    def test_mask_telefone(self):
        masked = mask_telefone('17999990000')
        self.assertIn('***', masked)


class SafeCompareTests(SimpleTestCase):
    def test_iguais(self):
        self.assertTrue(safe_str_compare('abc', 'abc'))

    def test_diferentes(self):
        self.assertFalse(safe_str_compare('abc', 'xyz'))

    def test_none(self):
        self.assertFalse(safe_str_compare(None, 'x'))


@override_settings(CLIENT_IP_HEADER='')
class ClientIpTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_remote_addr_default(self):
        req = self.rf.get('/')
        req.META['REMOTE_ADDR'] = '1.2.3.4'
        self.assertEqual(client_ip(req), '1.2.3.4')

    def test_x_forwarded_for_ignorado(self):
        # XFF e controlado pelo cliente: nunca vira o IP (burlaria rate-limit/auditoria)
        req = self.rf.get('/')
        req.META['HTTP_X_FORWARDED_FOR'] = '5.6.7.8, 9.9.9.9'
        req.META['REMOTE_ADDR'] = '127.0.0.1'
        self.assertEqual(client_ip(req), '127.0.0.1')

    def test_x_real_ip_ignorado_sem_header_configurado(self):
        # dev/testes: sem proxy na frente o header seria forjavel
        req = self.rf.get('/', HTTP_X_REAL_IP='8.8.8.8')
        req.META['REMOTE_ADDR'] = '10.0.0.1'
        self.assertEqual(client_ip(req), '10.0.0.1')

    def test_remote_addr_invalido_ou_vazio_cai_no_fallback(self):
        req = self.rf.get('/')
        req.META['REMOTE_ADDR'] = ''
        self.assertEqual(client_ip(req), '0.0.0.0')
        req.META['REMOTE_ADDR'] = 'lixo'
        self.assertEqual(client_ip(req), '0.0.0.0')

    def test_request_none(self):
        self.assertEqual(client_ip(None), '0.0.0.0')


@override_settings(CLIENT_IP_HEADER='HTTP_X_REAL_IP')
class ClientIpAtrasDoProxyTests(SimpleTestCase):
    """Railway: REMOTE_ADDR = proxy (igual p/ todos); IP real em X-Real-IP."""

    def setUp(self):
        self.rf = RequestFactory()

    def _req(self, **meta):
        req = self.rf.get('/', **meta)
        req.META['REMOTE_ADDR'] = '100.64.0.2'
        return req

    def test_usa_x_real_ip(self):
        self.assertEqual(client_ip(self._req(HTTP_X_REAL_IP='200.1.2.3')), '200.1.2.3')

    def test_visitantes_distintos_nao_compartilham_ip(self):
        a = client_ip(self._req(HTTP_X_REAL_IP='200.1.2.3'))
        b = client_ip(self._req(HTTP_X_REAL_IP='201.9.9.9'))
        self.assertNotEqual(a, b)

    def test_ipv6_normalizado(self):
        self.assertEqual(
            client_ip(self._req(HTTP_X_REAL_IP='2001:DB8::1')), '2001:db8::1',
        )

    def test_header_invalido_cai_no_remote_addr(self):
        # valor lixo nao pode chegar ao ratelimit (ip_network) nem ao inet do Postgres
        self.assertEqual(client_ip(self._req(HTTP_X_REAL_IP='abc')), '100.64.0.2')
        self.assertEqual(client_ip(self._req(HTTP_X_REAL_IP='')), '100.64.0.2')

    def test_header_ausente_cai_no_remote_addr(self):
        self.assertEqual(client_ip(self._req()), '100.64.0.2')

    def test_nome_do_header_em_formato_http(self):
        with override_settings(CLIENT_IP_HEADER='X-Real-IP'):
            self.assertEqual(client_ip(self._req(HTTP_X_REAL_IP='200.1.2.3')), '200.1.2.3')

    def test_ratelimit_e_axes_usam_client_ip(self):
        from axes.helpers import get_client_ip_address
        from django_ratelimit.core import _get_ip

        req = self._req(HTTP_X_REAL_IP='200.1.2.3')
        self.assertEqual(_get_ip(req), '200.1.2.3')
        self.assertEqual(get_client_ip_address(req), '200.1.2.3')
        # lixo no header nao derruba o ratelimit (ValueError em ip_network)
        self.assertEqual(_get_ip(self._req(HTTP_X_REAL_IP='x, y')), '100.64.0.2')


class SafeNextTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_interno_ok(self):
        req = self.rf.get('/')
        self.assertEqual(safe_next(req, '/profissional/?data=2026-01-01', 'x'), '/profissional/?data=2026-01-01')

    def test_externo_cai_no_fallback(self):
        req = self.rf.get('/')
        self.assertEqual(safe_next(req, 'https://evil.example/', 'aranha:profissional_agenda'),
                         'aranha:profissional_agenda')
        self.assertEqual(safe_next(req, '//evil.example/', 'f'), 'f')
        self.assertEqual(safe_next(req, '', 'f'), 'f')
