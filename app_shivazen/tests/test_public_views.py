"""Smoke tests para views publicas e healthcheck."""
from django.test import Client, TestCase
from django.urls import reverse


class PublicViewsTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_home(self):
        r = self.client.get(reverse('shivazen:inicio'))
        self.assertIn(r.status_code, (200, 301, 302))

    def test_healthcheck_ok(self):
        r = self.client.get('/health/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['status'], 'ok')
        self.assertTrue(body['db'])

    def test_manifest(self):
        r = self.client.get(reverse('shivazen:manifest'))
        self.assertEqual(r.status_code, 200)
