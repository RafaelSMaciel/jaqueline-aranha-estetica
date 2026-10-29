"""Regras operacionais: registro tipado de Configuracao (CHAVES + Configuracao.ler) e a tela do painel.

Antes a tela era chave-valor livre: listava marca e estado interno, criava chave inventada e so
validava email_admin (MAX_FALTAS_BLOQUEIO='abc' caia no padrao sem aviso).
"""
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from aranha_estetica.constants import MAX_FALTAS_ANTES_BLOQUEIO
from aranha_estetica.forms.cadastros import ConfiguracaoForm
from aranha_estetica.models import Configuracao, LogAuditoria, Usuario
from aranha_estetica.models.configuracao import CHAVES, chave_do_registro
from aranha_estetica.services import nps


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class RegistroTests(TestCase):
    def test_ler_devolve_o_valor_tipado(self):
        Configuracao.objects.create(chave='MAX_FALTAS_BLOQUEIO', valor=' 5 ')
        Configuracao.objects.create(chave='email_admin', valor='dona@clinica.test')
        self.assertEqual(Configuracao.ler('MAX_FALTAS_BLOQUEIO'), 5)
        self.assertEqual(Configuracao.ler('email_admin'), 'dona@clinica.test')

    def test_sem_linha_ou_invalido_vale_o_padrao_com_aviso(self):
        self.assertEqual(Configuracao.ler('MAX_FALTAS_BLOQUEIO'), MAX_FALTAS_ANTES_BLOQUEIO)
        self.assertEqual(Configuracao.ler('email_admin'), '')
        for invalido in ('abc', '0', '11', '٣', '2.5'):
            with self.subTest(valor=invalido):
                Configuracao.objects.update_or_create(chave='MAX_FALTAS_BLOQUEIO', defaults={'valor': invalido})
                with self.assertLogs('aranha_estetica.models.configuracao', 'WARNING'):
                    self.assertEqual(Configuracao.ler('MAX_FALTAS_BLOQUEIO'), MAX_FALTAS_ANTES_BLOQUEIO)

    def test_chave_em_outra_caixa_e_lida_e_a_exata_vence(self):
        Configuracao.objects.create(chave='EMAIL_ADMIN', valor='maiuscula@clinica.test')
        self.assertEqual(Configuracao.ler('email_admin'), 'maiuscula@clinica.test')
        Configuracao.objects.create(chave='email_admin', valor='exata@clinica.test')
        self.assertEqual(Configuracao.ler('email_admin'), 'exata@clinica.test')
        self.assertEqual(chave_do_registro(' max_faltas_bloqueio '), 'MAX_FALTAS_BLOQUEIO')
        self.assertIsNone(chave_do_registro('CLINIC_NAME'))

    def test_chave_fora_do_registro_e_erro_de_programacao(self):
        with self.assertRaises(KeyError):
            Configuracao.ler('CLINIC_NAME')

    @override_settings(ADMIN_EMAIL='env@clinica.test')
    def test_alerta_de_nps_le_pelo_registro(self):
        Configuracao.objects.create(chave='email_admin', valor='sem-arroba')   # gravado antes da validacao
        with self.assertLogs('aranha_estetica.models.configuracao', 'WARNING'):
            self.assertEqual(nps.destino_alerta(), 'env@clinica.test')
        Configuracao.objects.filter(chave='email_admin').update(valor='dona@clinica.test')
        self.assertEqual(nps.destino_alerta(), 'dona@clinica.test')


class ConfiguracaoFormTests(TestCase):
    def test_faltas_recusa_zero_texto_e_fora_da_faixa(self):
        for valor in ('0', 'abc', '', '11', '-1'):
            with self.subTest(valor=valor):
                form = ConfiguracaoForm({'chave': 'MAX_FALTAS_BLOQUEIO', 'valor': valor})
                self.assertFalse(form.is_valid())
                self.assertEqual(form.errors['valor'], ['Use um número inteiro de 1 a 10.'])
        form = ConfiguracaoForm({'chave': 'max_faltas_bloqueio', 'valor': '4'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data, {'chave': 'MAX_FALTAS_BLOQUEIO', 'valor': 4})

    def test_chave_livre_e_reservada_recusadas(self):
        form = ConfiguracaoForm({'chave': 'CLINIC_NAME', 'valor': 'Outra'})
        self.assertFalse(form.is_valid())
        self.assertIn('não é uma regra operacional', form.errors['chave'][0])
        form = ConfiguracaoForm({'chave': 'prontuario_perguntas', 'valor': '[]'})
        self.assertIn('Anamneses', form.errors['chave'][0])

    def test_email_em_branco_vale_e_invalido_nao(self):
        self.assertTrue(ConfiguracaoForm({'valor': ''}, chave='email_admin').is_valid())
        form = ConfiguracaoForm({'valor': 'sem-arroba'}, chave='EMAIL_ADMIN')
        self.assertEqual(form.errors['valor'], ['Informe um e-mail válido.'])


class RegrasOperacionaisTelaTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(email='dona-regras@test.com', password='senha-forte-123',
                                                      nome='Dona')
        self.client.force_login(self.admin)

    def test_lista_so_o_registro(self):
        Configuracao.objects.create(chave='CLINIC_NAME', valor='Clínica X')
        Configuracao.objects.create(chave='cursor_interno', valor='42')
        resp = self.client.get(reverse('aranha:admin_configuracoes'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([r['chave'] for r in resp.context['regras']], list(CHAVES))
        self.assertNotContains(resp, 'CLINIC_NAME')
        self.assertNotContains(resp, 'cursor_interno')
        self.assertContains(resp, 'Regras operacionais')
        self.assertNotContains(resp, 'name="descricao"')   # sem chave/descricao livres

    def test_nao_cria_chave_livre(self):
        resp = self.client.post(reverse('aranha:admin_criar_configuracao'), {'chave': 'nova_chave', 'valor': '1'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Configuracao.objects.filter(chave='nova_chave').exists())
        self.assertTrue(any('não é uma regra operacional' in m for m in _mensagens(resp)))

    def test_faltas_zero_ou_texto_recusado_com_o_erro_no_campo(self):
        url = reverse('aranha:admin_criar_configuracao')
        for valor in ('0', 'abc'):
            with self.subTest(valor=valor):
                resp = self.client.post(url, {'chave': 'MAX_FALTAS_BLOQUEIO', 'valor': valor})
                self.assertEqual(resp.status_code, 200)
                self.assertFalse(Configuracao.objects.filter(chave__iexact='MAX_FALTAS_BLOQUEIO').exists())
                self.assertContains(resp, 'id="regra-MAX_FALTAS_BLOQUEIO-erro"')
                self.assertContains(resp, 'aria-invalid="true"')
                rotulo = CHAVES['MAX_FALTAS_BLOQUEIO'].rotulo
                self.assertIn(f'{rotulo}: Use um número inteiro de 1 a 10.', _mensagens(resp))

    def test_gravar_editar_e_restaurar_padrao_auditados(self):
        self.client.post(reverse('aranha:admin_criar_configuracao'), {'chave': 'MAX_FALTAS_BLOQUEIO', 'valor': '4'})
        linha = Configuracao.objects.get(chave='MAX_FALTAS_BLOQUEIO')
        self.assertEqual((linha.valor, linha.descricao), ('4', CHAVES['MAX_FALTAS_BLOQUEIO'].descricao))
        self.assertEqual(Configuracao.ler('MAX_FALTAS_BLOQUEIO'), 4)
        resp = self.client.post(reverse('aranha:admin_editar_configuracao', args=[linha.pk]), {'valor': '11'})
        self.assertEqual(resp.status_code, 200)
        linha.refresh_from_db()
        self.assertEqual(linha.valor, '4')
        self.client.post(reverse('aranha:admin_editar_configuracao', args=[linha.pk]), {'valor': '6'})
        linha.refresh_from_db()
        self.assertEqual(linha.valor, '6')
        self.client.post(reverse('aranha:admin_excluir_configuracao', args=[linha.pk]))
        self.assertEqual(Configuracao.ler('MAX_FALTAS_BLOQUEIO'), MAX_FALTAS_ANTES_BLOQUEIO)
        self.assertEqual(LogAuditoria.objects.filter(tabela='configuracao', registro_id=linha.pk).count(), 3)

    def test_criar_regra_ja_gravada_atualiza_sem_duplicar(self):
        Configuracao.objects.create(chave='EMAIL_ADMIN', valor='antigo@clinica.test')
        self.client.post(reverse('aranha:admin_criar_configuracao'), {'chave': 'email_admin', 'valor': 'novo@clinica.test'})
        self.assertEqual(list(Configuracao.objects.values_list('chave', 'valor')), [('EMAIL_ADMIN', 'novo@clinica.test')])

    def test_marca_e_estado_interno_nao_editaveis_por_aqui(self):
        marca = Configuracao.objects.create(chave='CLINIC_NAME', valor='Clínica X')
        resp = self.client.post(reverse('aranha:admin_editar_configuracao', args=[marca.pk]), {'valor': 'Hack'})
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post(reverse('aranha:admin_excluir_configuracao', args=[marca.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Configuracao.objects.filter(pk=marca.pk, valor='Clínica X').exists())

    def test_valor_antigo_invalido_avisa_na_tela(self):
        Configuracao.objects.create(chave='MAX_FALTAS_BLOQUEIO', valor='abc')
        resp = self.client.get(reverse('aranha:admin_configuracoes'))
        self.assertContains(resp, 'está usando o padrão')

    def test_get_nas_acoes_nao_altera(self):
        linha = Configuracao.objects.create(chave='MAX_FALTAS_BLOQUEIO', valor='4')
        for url in (reverse('aranha:admin_criar_configuracao'),
                    reverse('aranha:admin_editar_configuracao', args=[linha.pk]),
                    reverse('aranha:admin_excluir_configuracao', args=[linha.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)
        self.assertTrue(Configuracao.objects.filter(pk=linha.pk).exists())
