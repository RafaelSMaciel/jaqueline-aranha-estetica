"""Testes de CodigoVerificacao: TTL, consumo atomico, e reuso."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from app_shivazen.models import CodigoVerificacao


class CodigoVerificacaoConsumirTests(TestCase):
    def setUp(self):
        self.telefone = '17999990001'
        self.codigo = '123456'
        self.cv = CodigoVerificacao.objects.create(
            telefone=self.telefone,
            codigo=self.codigo,
        )

    def test_consumir_valido_retorna_true(self):
        resultado = CodigoVerificacao.consumir(self.telefone, self.codigo)
        self.assertTrue(resultado)
        self.cv.refresh_from_db()
        self.assertTrue(self.cv.usado)

    def test_consumir_invalido_retorna_false(self):
        resultado = CodigoVerificacao.consumir(self.telefone, '000000')
        self.assertFalse(resultado)
        self.cv.refresh_from_db()
        self.assertFalse(self.cv.usado)

    def test_consumir_ja_usado_retorna_false(self):
        self.cv.usado = True
        self.cv.save()
        resultado = CodigoVerificacao.consumir(self.telefone, self.codigo)
        self.assertFalse(resultado)

    def test_consumir_expirado_retorna_false(self):
        # Simular criacao ha 15 minutos (TTL = 10 min)
        CodigoVerificacao.objects.filter(pk=self.cv.pk).update(
            criado_em=timezone.now() - timedelta(seconds=CodigoVerificacao.TTL_SEGUNDOS + 60)
        )
        resultado = CodigoVerificacao.consumir(self.telefone, self.codigo)
        self.assertFalse(resultado)

    def test_consumir_telefone_errado_retorna_false(self):
        resultado = CodigoVerificacao.consumir('17000000000', self.codigo)
        self.assertFalse(resultado)

    def test_consumir_nao_permite_duplo_uso(self):
        """Duas chamadas concorrentes: somente a primeira consome."""
        r1 = CodigoVerificacao.consumir(self.telefone, self.codigo)
        r2 = CodigoVerificacao.consumir(self.telefone, self.codigo)
        self.assertTrue(r1)
        self.assertFalse(r2)


class CodigoVerificacaoEstaValidoTests(TestCase):
    def test_esta_valido_dentro_do_ttl(self):
        cv = CodigoVerificacao.objects.create(telefone='17999990001', codigo='111111')
        self.assertTrue(cv.esta_valido)

    def test_esta_valido_falso_quando_usado(self):
        cv = CodigoVerificacao.objects.create(telefone='17999990001', codigo='222222', usado=True)
        self.assertFalse(cv.esta_valido)

    def test_esta_valido_falso_quando_expirado(self):
        cv = CodigoVerificacao.objects.create(telefone='17999990001', codigo='333333')
        CodigoVerificacao.objects.filter(pk=cv.pk).update(
            criado_em=timezone.now() - timedelta(seconds=CodigoVerificacao.TTL_SEGUNDOS + 60)
        )
        cv.refresh_from_db()
        self.assertFalse(cv.esta_valido)
