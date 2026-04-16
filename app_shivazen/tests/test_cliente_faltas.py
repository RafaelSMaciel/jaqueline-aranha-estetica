"""Testes de registrar_falta / resetar_faltas (3-strike)."""
from django.test import TestCase

from .factories import criar_cliente


class ClienteFaltasTests(TestCase):
    def test_registrar_falta_incrementa_contador(self):
        cliente = criar_cliente()
        cliente.registrar_falta()
        cliente.refresh_from_db()
        self.assertEqual(cliente.faltas_consecutivas, 1)
        self.assertFalse(cliente.bloqueado_online)

    def test_tres_faltas_bloqueiam_agendamento_online(self):
        cliente = criar_cliente()
        for _ in range(3):
            cliente.registrar_falta()
        cliente.refresh_from_db()
        self.assertEqual(cliente.faltas_consecutivas, 3)
        self.assertTrue(cliente.bloqueado_online)

    def test_quarta_falta_mantem_bloqueio(self):
        cliente = criar_cliente()
        for _ in range(4):
            cliente.registrar_falta()
        cliente.refresh_from_db()
        self.assertEqual(cliente.faltas_consecutivas, 4)
        self.assertTrue(cliente.bloqueado_online)

    def test_resetar_faltas_zera_contador_e_desbloqueia(self):
        cliente = criar_cliente()
        for _ in range(3):
            cliente.registrar_falta()
        cliente.resetar_faltas()
        cliente.refresh_from_db()
        self.assertEqual(cliente.faltas_consecutivas, 0)
        self.assertFalse(cliente.bloqueado_online)
