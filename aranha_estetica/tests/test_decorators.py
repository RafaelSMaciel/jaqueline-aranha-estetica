"""Tests for access control decorators."""
from django.test import Client, TestCase

from aranha_estetica.models import Profissional, Usuario


class StaffRequiredDecoratorTests(TestCase):
    """Tests that @staff_required blocks non-staff users."""

    def test_anonimo_redireciona(self):
        client = Client()
        resp = client.get('/painel/overview/')
        self.assertIn(resp.status_code, [302, 403])

    def test_usuario_recepcao_sem_admin_redireciona(self):
        user = Usuario.objects.create_user(email='normal@test.com', password='senha123')
        client = Client()
        client.force_login(user)
        resp = client.get('/painel/overview/')
        self.assertEqual(resp.status_code, 302)

    def test_admin_acessa(self):
        user = Usuario.objects.create_user(
            email='admin@test.com', password='senha123', papel=Usuario.PAPEL_ADMIN,
        )
        client = Client()
        client.force_login(user)
        resp = client.get('/painel/overview/')
        self.assertEqual(resp.status_code, 200)


class ProfissionalRequiredDecoratorTests(TestCase):
    """Tests that @profissional_required blocks non-professional users."""

    def test_profissional_ativo_acessa(self):
        prof = Profissional.objects.create(nome='Dra. Test', ativo=True)
        user = Usuario.objects.create_user(email='prof@test.com', password='senha123')
        user.profissional = prof
        user.save(update_fields=['profissional_id'])
        client = Client()
        client.force_login(user)
        resp = client.get('/profissional/')
        self.assertEqual(resp.status_code, 200)

    def test_profissional_inativo_bloqueado(self):
        prof = Profissional.objects.create(nome='Dra. Inativa', ativo=False)
        user = Usuario.objects.create_user(email='inativa@test.com', password='senha123')
        user.profissional = prof
        user.save(update_fields=['profissional_id'])
        client = Client()
        client.force_login(user)
        resp = client.get('/profissional/')
        self.assertEqual(resp.status_code, 302)

    def test_staff_acessa_portal_profissional(self):
        user = Usuario.objects.create_user(
            email='staff@test.com', password='senha123', papel=Usuario.PAPEL_ADMIN,
        )
        client = Client()
        client.force_login(user)
        resp = client.get('/profissional/', follow=True)
        self.assertEqual(resp.status_code, 200)
