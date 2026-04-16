"""Testes da inscricao publica na lista de espera."""
from datetime import date, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app_shivazen.models import Cliente, ListaEspera

from .factories import criar_procedimento, criar_profissional


@override_settings(RATELIMIT_ENABLE=False)
class ListaEsperaPublicaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.prof = criar_profissional()
        self.proc = criar_procedimento()
        self.url = reverse('shivazen:lista_espera_publica')

    def _post(self, **overrides):
        data = {
            'nome': 'Ana Cliente',
            'telefone': '17988880000',
            'procedimento': self.proc.pk,
            'data_desejada': (timezone.now().date() + timedelta(days=5)).isoformat(),
            'turno': 'TARDE',
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_get_renderiza_formulario(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Lista de Espera')
        self.assertContains(resp, self.proc.nome)

    def test_post_valido_cria_cliente_e_inscricao(self):
        resp = self._post()
        self.assertRedirects(resp, reverse('shivazen:lista_espera_sucesso'))
        self.assertEqual(Cliente.objects.filter(telefone='17988880000').count(), 1)
        self.assertEqual(ListaEspera.objects.count(), 1)

    def test_reaproveita_cliente_existente(self):
        Cliente.objects.create(nome_completo='Ana Cliente', telefone='17988880000')
        self._post()
        self.assertEqual(Cliente.objects.filter(telefone='17988880000').count(), 1)

    def test_rejeita_data_passada(self):
        resp = self._post(data_desejada=(timezone.now().date() - timedelta(days=1)).isoformat())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ListaEspera.objects.count(), 0)

    def test_rejeita_campos_obrigatorios_ausentes(self):
        resp = self._post(nome='')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ListaEspera.objects.count(), 0)

    def test_evita_inscricao_duplicada(self):
        self._post()
        self._post()  # mesma combinacao cliente+procedimento+data
        self.assertEqual(ListaEspera.objects.count(), 1)

    def test_turno_invalido_vira_nulo(self):
        self._post(turno='MADRUGADA')
        inscricao = ListaEspera.objects.first()
        self.assertIsNone(inscricao.turno_desejado)

    def test_profissional_opcional(self):
        self._post(profissional='')
        inscricao = ListaEspera.objects.first()
        self.assertIsNone(inscricao.profissional_desejado)
