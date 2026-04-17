"""Testes dos validators (CPF, telefone, datas, valor)."""
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from app_shivazen.validators import (
    validate_cpf,
    validate_data_nascimento,
    validate_maior_idade,
    validate_telefone_br,
    validate_valor_positivo,
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


class DataNascimentoTests(SimpleTestCase):
    def test_data_passada_ok(self):
        validate_data_nascimento(date(1990, 1, 1))

    def test_data_futura_falha(self):
        with self.assertRaises(ValidationError):
            validate_data_nascimento(date.today() + timedelta(days=1))

    def test_idade_excessiva_falha(self):
        with self.assertRaises(ValidationError):
            validate_data_nascimento(date.today() - timedelta(days=151 * 365))


class MaiorIdadeTests(SimpleTestCase):
    def test_maior_idade_ok(self):
        validate_maior_idade(date.today() - timedelta(days=20 * 365))

    def test_menor_idade_falha(self):
        with self.assertRaises(ValidationError):
            validate_maior_idade(date.today() - timedelta(days=10 * 365))


class ValorPositivoTests(SimpleTestCase):
    def test_positivo_ok(self):
        validate_valor_positivo(10)

    def test_zero_ok(self):
        validate_valor_positivo(0)

    def test_negativo_falha(self):
        with self.assertRaises(ValidationError):
            validate_valor_positivo(-5)
