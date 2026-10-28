"""Quem entra em cada area da equipe, com o destino exato do redirecionamento (um teste que aceita
'302 ou 403' passa com a regra quebrada)."""
from urllib.parse import quote

from django.test import TestCase, override_settings
from django.urls import reverse

from aranha_estetica.models import Usuario
from aranha_estetica.tests.factories import criar_cliente, criar_profissional

PAINEL = ('aranha:painel_overview', 'aranha:painel_agendamentos', 'aranha:painel_clientes',
          'aranha:painel_profissionais', 'aranha:admin_procedimentos', 'aranha:admin_auditoria')


@override_settings(ADMIN_2FA_OBRIGATORIO=False, PROFISSIONAL_2FA_OBRIGATORIO=False)
class PermissoesDoPainelTests(TestCase):
    def setUp(self):
        self.sem_papel = Usuario.objects.create_user(email='semPapel@clinica.test', password='x-senha-123', nome='S')
        self.prof = Usuario.objects.create_user(email='ana@clinica.test', password='x-senha-123', nome='Ana',
                                                papel=Usuario.PAPEL_PROFISSIONAL, profissional=criar_profissional())
        self.admin = Usuario.objects.create_user(email='dona@clinica.test', password='x-senha-123', nome='Dona',
                                                 papel=Usuario.PAPEL_ADMIN)

    def test_anonimo_vai_ao_login_com_next(self):
        for nome in PAINEL:
            url = reverse(nome)
            with self.subTest(rota=nome):
                self.assertRedirects(self.client.get(url), f"{reverse('aranha:usuario_login')}?next={quote(url)}",
                                     fetch_redirect_response=False)

    def test_usuario_sem_papel_volta_ao_site(self):
        self.client.force_login(self.sem_papel)
        for nome in PAINEL:
            with self.subTest(rota=nome):
                self.assertRedirects(self.client.get(reverse(nome)), reverse('aranha:inicio'),
                                     fetch_redirect_response=False)

    def test_profissional_volta_a_propria_agenda(self):
        self.client.force_login(self.prof)
        for nome in PAINEL:
            with self.subTest(rota=nome):
                self.assertRedirects(self.client.get(reverse(nome)), reverse('aranha:profissional_agenda'),
                                     fetch_redirect_response=False)

    def test_admin_entra(self):
        self.client.force_login(self.admin)
        for nome in PAINEL:
            with self.subTest(rota=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_prontuario_de_usuario_sem_papel_e_negado(self):
        self.client.force_login(self.sem_papel)
        url = reverse('aranha:prontuario_detalhe', kwargs={'cliente_id': criar_cliente().pk})
        self.assertEqual(self.client.get(url).status_code, 403)
