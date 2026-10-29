"""Site a partir do catalogo e de constantes (decisoes 10, 12, 46): FAQ gerado das regras,
especialidades por Procedimento.CATEGORIA_CHOICES e servicos faciais/corporais do catalogo."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from aranha_estetica.conteudo import faq
from aranha_estetica.views.publico import site

from .factories import criar_procedimento, criar_profissional


class FaqDasConstantesTests(TestCase):
    def test_faq_cita_a_janela_e_o_cancelamento_tardio_da_constante(self):
        texto = ' '.join(i['a'] for c in faq.montar_faq(canais_agendar=[], canais_exclusao=[]) for i in c['itens'])
        self.assertIn('24 h antes do horário', texto)
        self.assertIn('conta como falta', texto)
        with patch.object(faq, 'JANELA_MINIMA_ALTERACAO', timedelta(hours=48)):
            texto = ' '.join(i['a'] for c in faq.montar_faq(canais_agendar=[], canais_exclusao=[])
                             for i in c['itens'])
        self.assertIn('48 h antes do horário', texto)

    def test_home_usa_o_limite_de_faltas_efetivo(self):
        from aranha_estetica.models import Configuracao
        Configuracao.objects.update_or_create(chave='MAX_FALTAS_BLOQUEIO', defaults={'valor': '5'})
        self.assertContains(self.client.get(reverse('aranha:inicio')), 'depois de 5 faltas seguidas')


class CatalogoNoSiteTests(TestCase):
    def setUp(self):
        prof = criar_profissional()
        self.ativo = criar_procedimento(nome='Peeling Suave', categoria='FACIAL', profissional=prof)
        self.desativado = criar_procedimento(nome='Facial Antigo', categoria='FACIAL', profissional=prof)
        self.desativado.ativo = False
        self.desativado.save(update_fields=['ativo'])
        self.corporal = criar_procedimento(nome='Drenagem Relaxante', categoria='CORPORAL', profissional=prof)

    def test_servicos_faciais_mostra_o_catalogo_com_agendar_do_procedimento(self):
        resp = self.client.get(reverse('aranha:servicos_faciais'))
        self.assertContains(resp, 'Peeling Suave')
        self.assertNotContains(resp, 'Facial Antigo')        # desativado some da vitrine
        self.assertNotContains(resp, 'Drenagem Relaxante')   # outra categoria
        self.assertContains(resp, f'procedimento={self.ativo.pk}')

    def test_servicos_corporais(self):
        resp = self.client.get(reverse('aranha:servicos_corporais'))
        self.assertContains(resp, 'Drenagem Relaxante')
        self.assertNotContains(resp, 'Peeling Suave')

    def test_especialidades_seguem_as_categorias_do_model_com_rotulo_de_reserva(self):
        with patch.object(site, 'TITULOS_CATEGORIA', {}):
            resp = self.client.get(reverse('aranha:especialidades'))
        self.assertEqual([g['label'] for g in resp.context['grupos']], ['Facial', 'Corporal'])
        resp = self.client.get(reverse('aranha:especialidades'))
        self.assertEqual([g['label'] for g in resp.context['grupos']], ['Tratamentos Faciais', 'Tratamentos Corporais'])

    @override_settings(DEBUG=False)
    def test_erro_de_banco_nao_vira_catalogo_vazio(self):
        from django.db import OperationalError
        with patch('aranha_estetica.views.publico.site.Procedimento.objects.filter',
                   side_effect=OperationalError('banco fora')), self.assertRaises(OperationalError):
            self.client.get(reverse('aranha:especialidades'))
