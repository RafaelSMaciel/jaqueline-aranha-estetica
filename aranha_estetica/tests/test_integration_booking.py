"""Integration tests for the full booking flow."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import Atendimento, Cliente, Feriado

from .factories import (
    criar_cliente,
    criar_procedimento,
    criar_profissional,
)
from .test_confirmar_agendamento import slot_local, verificar_sessao

CONFIRMAR_URL = 'aranha:confirmar_agendamento'


def _future_datetime_iso(days=2, hour=10):
    """ISO de um horario LOCAL futuro dentro do expediente da factory (09-18h)."""
    return slot_local(dias=days, hora=hour)


@override_settings(
    RATELIMIT_ENABLE=False,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=False,
)
@patch('aranha_estetica.utils.email.enviar_confirmacao_agendamento_email', return_value=True)
class IntegrationBookingFlowTests(TestCase):
    """End-to-end tests exercising the booking view via Django test client."""

    def setUp(self):
        cache.clear()
        # Feriados seeded via migration 0012 pode colidir com datas dos testes.
        Feriado.objects.all().delete()
        self.client = Client()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('150.00'))
        self.url = reverse(CONFIRMAR_URL)

    def _post(self, mock_email=None, with_otp=True, **overrides):
        """POST to confirmar_agendamento with sensible defaults.

        with_otp=True simula o celular ja verificado por SMS no wizard (sessao
        presa ao telefone) — exigido de TODO agendamento (novo ou recorrente).
        """
        data = {
            'nome': 'Ana Integracao',
            'telefone': '17988881111',
            'data_nascimento': '1990-03-20',
            'procedimento': self.proc.pk,
            'profissional': self.prof.pk,
            'datetime': _future_datetime_iso(),
        }
        data.update(overrides)
        if with_otp:
            verificar_sessao(self.client, data['telefone'])
        return self.client.post(self.url, data)

    # ------------------------------------------------------------------
    # 1. Full happy-path booking flow
    # ------------------------------------------------------------------
    def test_fluxo_completo_agendamento(self, mock_email):
        """
        POST valid data → client created, Atendimento at PENDENTE.
        Then drive the real FSM: PENDENTE → CONFIRMADO → REALIZADO via the
        domain methods (confirmar / marcar_realizado), exercising the actual
        transition table.
        REALIZADO must reset faltas_consecutivas to 0 as a SIDE EFFECT of the
        real transition (post_save signal), not via a manual resetar_faltas call.
        """
        # Act: submit booking
        resp = self._post()

        # Redirect to success page
        self.assertEqual(resp.status_code, 302)
        self.assertIn('sucesso', resp.url)

        # Cliente was created
        clientes = Cliente.objects.filter(telefone='17988881111')
        self.assertEqual(clientes.count(), 1, 'Exactly one client should exist')
        cliente = clientes.first()
        self.assertEqual(cliente.nome, 'Ana Integracao')

        # Atendimento was created with PENDENTE status
        atendimentos = Atendimento.objects.filter(cliente=cliente)
        self.assertEqual(atendimentos.count(), 1, 'Exactly one Atendimento should exist')
        atd = atendimentos.first()
        self.assertEqual(atd.status, 'PENDENTE')
        self.assertEqual(atd.procedimento, self.proc)
        self.assertEqual(atd.profissional, self.prof)

        # Drive the real FSM: PENDENTE → CONFIRMADO
        atd.confirmar()
        atd.refresh_from_db()
        self.assertEqual(atd.status, 'CONFIRMADO')

        # Give the client some faltas BEFORE the realizado transition so we can
        # observe the automatic reset triggered by the transition itself.
        cliente.faltas_consecutivas = 2
        cliente.save()

        # CONFIRMADO → REALIZADO via the domain method. This fires the post_save
        # signal whose side effect resets the client's faltas — we must NOT call
        # resetar_faltas() by hand, that is what we are verifying.
        atd.marcar_realizado()
        atd.refresh_from_db()
        self.assertEqual(atd.status, 'REALIZADO')

        cliente.refresh_from_db()
        self.assertEqual(
            cliente.faltas_consecutivas, 0,
            'REALIZADO transition must reset faltas to 0 (signal side effect)',
        )
        self.assertFalse(cliente.bloqueado_online, 'Client should not be blocked after showing up')

    # ------------------------------------------------------------------
    # 2. Reuse existing client (same phone, no duplicate)
    # ------------------------------------------------------------------
    def test_agendamento_reusa_cliente_existente(self, mock_email):
        """
        If a client with the same phone already exists, the view must
        reuse it (get_or_create) — not create a duplicate.
        """
        # Pre-create a client with the exact phone number we'll use
        existing = criar_cliente(
            nome='Nome Antigo',
            telefone='17988882222',
            data_nascimento=None,
        )
        pre_count = Cliente.objects.filter(telefone='17988882222').count()
        self.assertEqual(pre_count, 1)

        # Book with the same phone (different slot to avoid conflicts).
        # Cliente recorrente -> precisa do OTP verificado (gate anti-sequestro).
        resp = self._post(
            telefone='17988882222',
            nome='Nome Atualizado',
            datetime=_future_datetime_iso(days=3, hour=11),
            with_otp=True,
        )
        self.assertEqual(resp.status_code, 302)

        # Still only one client with that phone
        clients_after = Cliente.objects.filter(telefone='17988882222')
        self.assertEqual(
            clients_after.count(), 1,
            'No duplicate client should be created for an existing phone number'
        )

        # The Atendimento was linked to the existing client
        atd = Atendimento.objects.filter(cliente__telefone='17988882222').first()
        self.assertIsNotNone(atd, 'An Atendimento should have been created')
        self.assertEqual(atd.cliente.pk, existing.pk)

        # The name was updated on the existing client (view updates name when different)
        existing.refresh_from_db()
        self.assertEqual(existing.nome, 'Nome Atualizado')

    # ------------------------------------------------------------------
    # 2b. Existing client WITHOUT OTP is blocked (anti-sequestro gate)
    # ------------------------------------------------------------------
    def test_cliente_existente_sem_otp_e_bloqueado(self, mock_email):
        """Espelho NEGATIVO do gate anti-sequestro (telefone verificado na sessao).

        Cliente recorrente (telefone ja cadastrado) que NAO verificou o OTP por
        SMS deve ser BLOQUEADO: redirect de volta ao formulario (nao a sucesso),
        sem criar Atendimento e sem sobrescrever o cadastro alheio. Sem este
        teste o gate poderia quebrar silenciosamente (so o caminho feliz
        with_otp=True era exercitado).
        """
        existing = criar_cliente(
            nome='Dona Original',
            telefone='17988885555',
            data_nascimento=None,
        )

        # POST recorrente SEM OTP verificado na sessao.
        resp = self._post(
            telefone='17988885555',
            nome='Tentativa Sequestro',
            datetime=_future_datetime_iso(days=4, hour=9),
            with_otp=False,
        )

        # Redireciona (nao 500) e NAO para a pagina de sucesso.
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn(
            'sucesso', resp.url,
            'Cliente existente sem OTP nao pode chegar a pagina de sucesso',
        )

        # Nenhum Atendimento criado para o cliente existente.
        self.assertEqual(
            Atendimento.objects.filter(cliente=existing).count(), 0,
            'Cliente existente sem OTP nao pode gerar agendamento',
        )

        # Cadastro intocado — o nome NAO foi sobrescrito pela tentativa.
        existing.refresh_from_db()
        self.assertEqual(
            existing.nome, 'Dona Original',
            'O gate deve barrar antes de qualquer escrita no cadastro alheio',
        )

    def test_telefone_novo_sem_otp_e_bloqueado(self, mock_email):
        """Front exige OTP de todo mundo — o servidor tambem (antes so recorrente)."""
        resp = self._post(telefone='17988887777', with_otp=False)
        self.assertNotIn('sucesso', resp.url)
        self.assertFalse(Cliente.objects.filter(telefone='17988887777').exists())
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_telefone_alterado_apos_verificacao_e_bloqueado(self, mock_email):
        verificar_sessao(self.client, '17988881111')
        resp = self._post(telefone='17988886666', with_otp=False)
        self.assertNotIn('sucesso', resp.url)
        self.assertEqual(Atendimento.objects.count(), 0)

    # ------------------------------------------------------------------
    # 3. Past datetime is rejected
    # ------------------------------------------------------------------
    def test_agendamento_data_futura_obrigatoria(self, mock_email):
        """
        A booking with a past datetime must be rejected: the view must NOT
        redirect to the success page and must NOT persist any Atendimento in
        the past. These invariants are asserted unconditionally — if the view
        has no past-date guard, this test should fail (red) and expose the bug,
        not silently pass.
        """
        past_dt = (timezone.now() - timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        ).isoformat()

        resp = self._post(datetime=past_dt)

        # Must redirect (not 200 — no success page should be served) and never 500
        self.assertEqual(resp.status_code, 302)

        # The redirect must NOT be to the success page — a past date is invalid.
        self.assertNotIn(
            'sucesso', resp.url,
            'A past datetime must not lead to the booking success page',
        )

        # No Atendimento may be persisted with a past start datetime.
        self.assertEqual(
            Atendimento.objects.filter(
                data_hora_inicio__lt=timezone.now()
            ).count(),
            0,
            'No Atendimento should be created for a past datetime',
        )

    def test_consent_email_marketing_captura(self, mock_email):
        """POST com consent_email_marketing=on salva True + timestamp + IP."""
        resp = self._post(
            telefone='17988883333',
            consent_email_marketing='on',
            email='ana@example.com',
        )
        self.assertEqual(resp.status_code, 302)
        cli = Cliente.objects.get(telefone='17988883333')
        self.assertTrue(cli.consent_email_marketing)
        self.assertIsNotNone(cli.consent_email_marketing_em)

    def test_consent_whatsapp_nps_opt_out_default(self, mock_email):
        """POST sem consent_whatsapp_nps mantem False (opt-in required)."""
        resp = self._post(telefone='17988884444')
        self.assertEqual(resp.status_code, 302)
        cli = Cliente.objects.get(telefone='17988884444')
        self.assertFalse(cli.consent_whatsapp_nps)
        self.assertFalse(cli.consent_email_marketing)
