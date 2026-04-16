"""Testes do fluxo publico de agendamento."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app_shivazen.models import Atendimento, Cliente

from .factories import (
    criar_procedimento,
    criar_profissional,
)


@override_settings(RATELIMIT_ENABLE=False)
@patch('app_shivazen.utils.whatsapp.enviar_whatsapp', return_value=True)
class ConfirmarAgendamentoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('120.00'))

        base = (timezone.now() + timedelta(days=2)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        self.datetime_str = base.isoformat()
        self.url = reverse('shivazen:confirmar_agendamento')

    def _post(self, **overrides):
        data = {
            'nome': 'Maria Teste',
            'telefone': '17999991111',
            'data_nascimento': '1990-06-15',
            'procedimento': self.proc.pk,
            'profissional': self.prof.pk,
            'datetime': self.datetime_str,
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_cria_cliente_novo_e_atendimento(self, _):
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Cliente.objects.filter(telefone='17999991111').count(), 1)
        self.assertEqual(Atendimento.objects.count(), 1)

        atd = Atendimento.objects.first()
        self.assertEqual(atd.status, 'AGENDADO')
        self.assertEqual(atd.cliente.nome_completo, 'Maria Teste')
        self.assertEqual(atd.valor_cobrado, Decimal('120.00'))

    def test_reutiliza_cliente_existente_e_atualiza_nome(self, _):
        Cliente.objects.create(nome_completo='Antigo Nome', telefone='17999991111')
        self._post(nome='Nome Novo')

        clientes = Cliente.objects.filter(telefone='17999991111')
        self.assertEqual(clientes.count(), 1)
        self.assertEqual(clientes.first().nome_completo, 'Nome Novo')

    def test_rejeita_quando_campos_faltando(self, _):
        resp = self._post(telefone='')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_rejeita_conflito_de_horario(self, _):
        self._post()
        self.assertEqual(Atendimento.objects.count(), 1)

        # Tenta agendar exatamente no mesmo horario
        self._post(telefone='17999992222', nome='Outra Cliente')
        self.assertEqual(Atendimento.objects.count(), 1)  # Nao foi criado

    def test_get_redireciona_para_agendamento(self, _):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('agendamento', resp.url)

    def test_gera_token_de_cancelamento(self, _):
        self._post()
        atd = Atendimento.objects.first()
        self.assertIsNotNone(atd.token_cancelamento)
        self.assertEqual(len(atd.token_cancelamento), 43)  # token_urlsafe(32)
