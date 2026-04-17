"""Testes do modulo utils.security (PII masking + comparison)."""
from django.test import RequestFactory, SimpleTestCase

from app_shivazen.utils.security import (
    client_ip,
    mask_cpf,
    mask_email,
    mask_telefone,
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


class ClientIpTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_remote_addr_default(self):
        req = self.rf.get('/')
        req.META['REMOTE_ADDR'] = '1.2.3.4'
        self.assertEqual(client_ip(req), '1.2.3.4')

    def test_x_forwarded_for(self):
        req = self.rf.get('/')
        req.META['HTTP_X_FORWARDED_FOR'] = '5.6.7.8, 9.9.9.9'
        req.META['REMOTE_ADDR'] = '127.0.0.1'
        self.assertEqual(client_ip(req), '5.6.7.8')
