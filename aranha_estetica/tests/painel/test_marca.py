"""Identidade visual pelo MarcaForm: as regras de antes (_validar) no clean e a recusa sem gravar nada."""
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from aranha_estetica.forms.cadastros import MarcaForm
from aranha_estetica.models import Configuracao, Usuario
from aranha_estetica.utils.branding import get_branding


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class MarcaFormTests(TestCase):
    def setUp(self):
        cache.clear()

    def _form(self, **dados):
        return MarcaForm(dados, efetivos=get_branding(), salvos={})

    def test_normaliza_cnpj_cpf_e_whatsapp(self):
        form = self._form(CLINIC_CNPJ='12345678000199', ENCARREGADO_NOME='Jaqueline',
                          WHATSAPP_NUMERO='(17) 99123-4567')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.alteracoes, {'CLINIC_CNPJ': '12.345.678/0001-99', 'ENCARREGADO_NOME': 'Jaqueline',
                                           'WHATSAPP_NUMERO': '5517991234567'})
        cpf = self._form(CLINIC_CNPJ='52998224725')
        self.assertTrue(cpf.is_valid(), cpf.errors)
        self.assertEqual(cpf.alteracoes, {'CLINIC_CNPJ': '529.982.247-25'})

    def test_recusas_com_as_mensagens_de_antes(self):
        form = self._form(WHATSAPP_NUMERO='123', CLINIC_EMAIL='x', ENCARREGADO_EMAIL='y', CLINIC_CNPJ='1',
                          INSTAGRAM_URL='instagram', THEME_COLOR='azul')
        self.assertFalse(form.is_valid())
        self.assertEqual({campo: erros[0] for campo, erros in form.errors.items()}, {
            'WHATSAPP_NUMERO': 'WhatsApp: informe DDD + número (ex: 17 99123-4567) ou com DDI (5517991234567).',
            'CLINIC_EMAIL': 'E-mail de contato inválido.',
            'ENCARREGADO_EMAIL': 'E-mail do encarregado de dados inválido.',
            'CLINIC_CNPJ': 'CNPJ (14 dígitos) ou CPF (11 dígitos) inválido.',
            'INSTAGRAM_URL': 'URL do Instagram inválida (ex: https://instagram.com/suaclinica).',
            'THEME_COLOR': 'Cor da marca: use o formato hexadecimal #RRGGBB.',
        })

    @override_settings(MARCA_ENV={'WHATSAPP_NUMERO': '123'})
    def test_campo_igual_ao_em_uso_nao_e_validado_nem_alteracao(self):
        em_uso = get_branding()
        form = MarcaForm({'WHATSAPP_NUMERO': em_uso['WHATSAPP_NUMERO'], 'THEME_COLOR': em_uso['THEME_COLOR'],
                          'CLINIC_PHONE': '(17) 3333-4444'}, efetivos=em_uso, salvos={})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.alteracoes, {'CLINIC_PHONE': '(17) 3333-4444'})

    def test_vazio_so_e_alteracao_se_havia_ajuste(self):
        em_uso = {**get_branding(), 'CLINIC_PHONE': '(17) 3333-4444'}
        sem_ajuste = MarcaForm({'CLINIC_PHONE': ''}, efetivos=em_uso, salvos={})   # valor vem do env
        self.assertTrue(sem_ajuste.is_valid())
        self.assertEqual(sem_ajuste.alteracoes, {})
        form = MarcaForm({'CLINIC_PHONE': ''}, efetivos=em_uso, salvos={'CLINIC_PHONE': '(17) 3333-4444'})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.alteracoes, {'CLINIC_PHONE': ''})


class MarcaTelaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = Usuario.objects.create_superuser(email='dona-marca@test.com', password='senha-forte-123',
                                                      nome='Dona')
        self.client.force_login(self.admin)

    def test_recusa_nao_grava_nada_e_volta_com_o_digitado(self):
        resp = self.client.post(reverse('aranha:admin_branding'), {
            'CLINIC_PHONE': '(17) 3333-4444', 'CLINIC_CNPJ': '123'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Configuracao.objects.filter(chave__in=['CLINIC_PHONE', 'CLINIC_CNPJ']).exists())
        self.assertIn('CNPJ (14 dígitos) ou CPF (11 dígitos) inválido.', _mensagens(resp))
        self.assertContains(resp, 'value="(17) 3333-4444"')
        self.assertContains(resp, 'id="campo_CLINIC_CNPJ-erro"')

    def test_valido_grava_normalizado_e_audita(self):
        resp = self.client.post(reverse('aranha:admin_branding'), {'CLINIC_CNPJ': '12345678000199'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Configuracao.objects.get(chave='CLINIC_CNPJ').valor, '12.345.678/0001-99')
        self.assertTrue(any('Branding atualizado (1 campo)' in m for m in _mensagens(resp)))
        resp = self.client.post(reverse('aranha:admin_branding'), {'CLINIC_CNPJ': '12.345.678/0001-99'})
        self.assertIn('Nenhuma alteração detectada.', _mensagens(resp))
