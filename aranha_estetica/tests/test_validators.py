"""Testes dos validators (CPF, telefone, data de nascimento)."""
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.utils import timezone

from aranha_estetica.validators import (
    normalizar_cpf,
    normalizar_telefone,
    validate_cpf,
    validate_data_nascimento,
    validate_telefone_br,
)


class CpfValidatorTests(SimpleTestCase):
    def test_cpf_valido(self):
        validate_cpf('52998224725')
        validate_cpf('529.982.247-25')

    def test_cpf_invalido_dv(self):
        with self.assertRaises(ValidationError):
            validate_cpf('52998224700')

    def test_cpf_repetido(self):
        with self.assertRaises(ValidationError):
            validate_cpf('11111111111')

    def test_cpf_tamanho_errado(self):
        with self.assertRaises(ValidationError):
            validate_cpf('123')


class TelefoneValidatorTests(SimpleTestCase):
    def test_celular_11_digitos(self):
        validate_telefone_br('17999990000')

    def test_fixo_10_digitos(self):
        validate_telefone_br('1733330000')

    def test_invalido(self):
        with self.assertRaises(ValidationError):
            validate_telefone_br('123')

    def test_aceita_ddi_55(self):
        # autofill do navegador preenche +55: o banco (PG) guarda sem DDI
        validate_telefone_br('+55 (17) 99999-0001')

    def test_sem_ddd_invalido(self):
        with self.assertRaises(ValidationError):
            validate_telefone_br('99999-0001')


class NormalizarTelefoneTests(SimpleTestCase):
    def test_remove_mascara_ddi_e_zero_de_tronco(self):
        self.assertEqual(normalizar_telefone('+55 (17) 99999-0001'), '17999990001')
        self.assertEqual(normalizar_telefone('55 17 3333-0000'), '1733330000')
        self.assertEqual(normalizar_telefone('017 99999-0001'), '17999990001')
        self.assertEqual(normalizar_telefone('(17) 99999-0001'), '17999990001')

    def test_ddd_55_nao_e_confundido_com_ddi(self):
        self.assertEqual(normalizar_telefone('(55) 99999-0000'), '55999990000')
        self.assertEqual(normalizar_telefone('55 3222-0000'), '5532220000')

    def test_nao_valida_tamanho(self):
        self.assertEqual(normalizar_telefone('99999-0001'), '999990001')
        self.assertEqual(normalizar_telefone(None), '')


class CpfHelpersTests(SimpleTestCase):
    def test_normalizar_cpf(self):
        self.assertEqual(normalizar_cpf('529.982.247-25'), '52998224725')
        self.assertEqual(normalizar_cpf(None), '')


class DataNascimentoTests(SimpleTestCase):
    def test_data_passada_ok(self):
        validate_data_nascimento(date(1990, 1, 1))

    def test_data_futura_falha(self):
        with self.assertRaises(ValidationError):
            validate_data_nascimento(timezone.localdate() + timedelta(days=1))

    def test_idade_excessiva_falha(self):
        with self.assertRaises(ValidationError):
            validate_data_nascimento(timezone.localdate() - timedelta(days=151 * 365))
