"""Login da equipe contra forca bruta sem virar negacao de servico contra a dona."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

SENHA = 'Spa-Zen#2026-forte'


@override_settings(RATELIMIT_ENABLE=False, AXES_FAILURE_LIMIT=5, ADMIN_2FA_OBRIGATORIO=False)
class BloqueioDoAxesTests(TestCase):
    """Decisao 42: o axes bloqueia por IP+usuario e por IP, nao por usuario sozinho."""

    def setUp(self):
        cache.clear()
        Usuario = get_user_model()
        self.email = 'dona@clinica.test'
        Usuario.objects.create_user(email=self.email, password=SENHA, nome='Dona', papel=Usuario.PAPEL_ADMIN)
        self.url = reverse('aranha:usuario_login')

    def _login(self, senha, ip):
        return self.client.post(self.url, {'username': self.email, 'password': senha}, REMOTE_ADDR=ip)

    def test_falhas_de_ips_diferentes_nao_trancam_a_dona(self):
        for ip in ('203.0.113.1', '203.0.113.2', '203.0.113.3', '203.0.113.4', '203.0.113.5'):
            self._login('senha-errada-123', ip)
        resp = self._login(SENHA, '198.51.100.7')
        self.assertRedirects(resp, reverse('aranha:painel_overview'), fetch_redirect_response=False)
        self.assertIn('_auth_user_id', self.client.session)

    def test_cinco_falhas_do_mesmo_ip_bloqueiam_esse_ip(self):
        for _ in range(5):
            self._login('senha-errada-123', '203.0.113.9')
        bloqueado = self._login(SENHA, '203.0.113.9')
        self.assertEqual(bloqueado.status_code, 429)
        self.assertNotIn('_auth_user_id', self.client.session)
        # outro IP continua entrando
        self.assertRedirects(self._login(SENHA, '198.51.100.7'), reverse('aranha:painel_overview'),
                             fetch_redirect_response=False)

    def test_falha_loga_evento_sem_o_email_em_claro(self):
        with self.assertLogs('aranha_estetica.views.conta.autenticacao', 'WARNING') as logs:
            self._login('senha-errada-123', '203.0.113.1')
        registro = logs.records[0]
        self.assertEqual(registro.getMessage(), 'login_falho')
        self.assertEqual(registro.email, 'do***@clinica.test')
