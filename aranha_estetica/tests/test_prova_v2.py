from django.test import TestCase, override_settings
from django.urls import reverse


class ProvaV2Tests(TestCase):
    @override_settings(DEBUG=True)
    def test_200_usa_casca_e_componentes(self):
        resp = self.client.get(reverse('aranha:prova_v2'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-theme=')   # casca base_v2
        self.assertContains(resp, 'bg-marca-forte')  # c-botao solido
        self.assertContains(resp, 'Prova da fundação')

    def test_404_em_producao(self):
        # Pagina de prova nao deve existir com DEBUG=False (producao).
        resp = self.client.get(reverse('aranha:prova_v2'))
        self.assertEqual(resp.status_code, 404)
