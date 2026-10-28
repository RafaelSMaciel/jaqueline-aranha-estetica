"""utils/telefone: fonte unica do telefone BR (normalizacao, validacao, E.164, link e mascara).

As tabelas documentam o contrato; os casos 'antes x depois' registram o que mudou na unificacao
(antes eram 7 implementacoes: o WhatsApp aceitava numero que o SMS recusava)."""
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from aranha_estetica.utils import telefone
from aranha_estetica.utils.branding import normalizar_whatsapp
from aranha_estetica.utils.sms import formatar_telefone as destino_sms
from aranha_estetica.utils.whatsapp import formatar_telefone as destino_whatsapp


class NormalizarTests(SimpleTestCase):
    def test_tabela(self):
        casos = [
            ('+55 (17) 99999-0001', '17999990001'),   # DDI do autofill
            ('017 3333-0000', '1733330000'),          # zero de tronco
            ('55999990000', '55999990000'),           # DDD 55 (RS) nao e DDI
            ('(17) 99999-0001', '17999990001'),
            ('', ''), (None, ''), ('abc', ''),
            ('999', '999'),                           # nao valida tamanho
        ]
        for valor, esperado in casos:
            with self.subTest(valor=valor):
                self.assertEqual(telefone.normalizar(valor), esperado)


class ValidacaoTests(SimpleTestCase):
    def test_valido_ou_vazio(self):
        casos = [
            ('17999990001', '17999990001'), ('(17) 3333-4444', '1733334444'), ('+55 17 99999-0001', '17999990001'),
            ('99999-0001', ''), ('(00) 99999-0000', ''), ('(10) 99999-0000', ''),
            ('+1 415 555 0100 99', ''), ('', ''), (None, ''),
        ]
        for valor, esperado in casos:
            with self.subTest(valor=valor):
                self.assertEqual(telefone.valido_ou_vazio(valor), esperado)

    def test_validar_levanta_com_mensagem_pronta(self):
        self.assertEqual(telefone.validar('(17) 99999-0001'), '17999990001')
        with self.assertRaises(ValidationError) as ctx:
            telefone.validar('123')
        self.assertEqual(ctx.exception.messages, [telefone.MSG_INVALIDO])

    def test_celular(self):
        self.assertTrue(telefone.eh_celular('(17) 99999-0001'))
        self.assertTrue(telefone.eh_celular('+55 17 99999-0001'))
        self.assertFalse(telefone.eh_celular('1733334444'))      # fixo nao recebe SMS
        self.assertFalse(telefone.eh_celular('0799999000'))


class DestinoTests(SimpleTestCase):
    def test_e164_e_href_tel(self):
        casos = [
            ('17999990001', '5517999990001', '+5517999990001'),
            ('(17) 3222-1111', '551732221111', '+551732221111'),
            ('5517999990001', '5517999990001', '+5517999990001'),
            ('017 99999-0001', '5517999990001', '+5517999990001'),
            ('14155550100123', '', ''), ('12345', '', ''), ('', '', ''),
        ]
        for valor, e164, href in casos:
            with self.subTest(valor=valor):
                self.assertEqual(telefone.e164(valor), e164)
                self.assertEqual(telefone.href_tel(valor), href)

    def test_sms_whatsapp_e_marca_usam_a_mesma_regra(self):
        for valor in ('17999990001', '(17) 3222-1111', '14155550100123', '017 99999-0001', '5510999990000'):
            with self.subTest(valor=valor):
                esperado = telefone.e164(valor)
                self.assertEqual(destino_sms(valor), esperado)
                self.assertEqual(destino_whatsapp(valor), esperado)
                self.assertEqual(normalizar_whatsapp(valor), esperado)

    def test_antes_x_depois_da_unificacao(self):
        # antes o WhatsApp mandava para qualquer sequencia de digitos e a marca aceitava 12-13
        # digitos de qualquer pais; agora so telefone BR valido (o SMS ja era assim)
        self.assertEqual(destino_whatsapp('14155550100123'), '')
        self.assertEqual(normalizar_whatsapp('4915112345678'), '')
        # DDD inexistente (10) era aceito com o DDI; agora e recusado nos tres canais
        self.assertEqual(destino_sms('5510999990000'), '')
        # zero de tronco era recusado pelo SMS ('0' + 11 digitos); agora vira o canonico
        self.assertEqual(destino_sms('017999990001'), '5517999990001')


class MascararTests(SimpleTestCase):
    def test_tabela(self):
        for valor, esperado in (('17999990000', '17****0000'), ('(17) 99999-0000', '17****0000'),
                                ('123', '***'), ('', ''), (None, '')):
            with self.subTest(valor=valor):
                self.assertEqual(telefone.mascarar(valor), esperado)
