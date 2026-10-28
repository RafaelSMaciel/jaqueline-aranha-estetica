"""Feed ICS do profissional: o calendario externo (operador fora da clinica) recebe so o nome publico
da cliente, nunca o nome completo."""
from django.test import TestCase
from django.urls import reverse

from aranha_estetica.tests.factories import (
    criar_atendimento, criar_cliente, criar_link, criar_procedimento, criar_profissional,
)
from aranha_estetica.utils.pii import nome_publico


class FeedIcsNomePublicoTests(TestCase):
    def test_evento_leva_primeiro_nome_inicial_e_procedimento(self):
        prof = criar_profissional()
        proc = criar_procedimento(nome='Limpeza de Pele')
        criar_atendimento(criar_cliente(nome='Maria Aparecida da Silva'), prof, proc)
        resp = self.client.get(reverse('aranha:ics_feed_profissional', args=[prof.slug]),
                               {'token': criar_link('FEED_ICS', prof)})
        self.assertEqual(resp.status_code, 200)
        ics = resp.content.decode()
        self.assertIn('SUMMARY:Maria S. - Limpeza de Pele', ics)
        self.assertNotIn('Aparecida', ics)
        self.assertNotIn('Silva', ics)


class NomePublicoTests(TestCase):
    def test_tabela(self):
        for nome, esperado in (('Maria da Silva', 'Maria S.'), ('Ana', 'Ana'), ('', 'Cliente'),
                               (None, 'Cliente'), ('[ANONIMIZADO-7]', 'Cliente'), ('joana souza', 'joana S.')):
            with self.subTest(nome=nome):
                self.assertEqual(nome_publico(nome), esperado)
