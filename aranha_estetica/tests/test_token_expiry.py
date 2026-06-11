"""Testes do fluxo OTP telefone-only (OtpCode.gerar_sms/verificar_sms).

Substitui os testes do legado CodigoVerificacao (removido na remodelagem
v2.1 fase 1b — guardava codigo em texto plano).
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import OtpCode


class OtpSmsVerificarTests(TestCase):
    def setUp(self):
        self.telefone = '17999990001'
        self.codigo, self.obj = OtpCode.gerar_sms(
            self.telefone, proposito=OtpCode.PROPOSITO_LOGIN,
        )

    def test_verificar_valido_retorna_ok(self):
        ok, motivo = OtpCode.verificar_sms(
            self.telefone, self.codigo, proposito=OtpCode.PROPOSITO_LOGIN,
        )
        self.assertTrue(ok)
        self.assertEqual(motivo, 'ok')
        self.obj.refresh_from_db()
        self.assertIsNotNone(self.obj.usado_em)

    def test_verificar_codigo_errado_retorna_false_e_conta_tentativa(self):
        ok, motivo = OtpCode.verificar_sms(
            self.telefone, '000000', proposito=OtpCode.PROPOSITO_LOGIN,
        )
        self.assertFalse(ok)
        self.assertTrue(motivo.startswith('incorreto'))
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.tentativas, 1)
        self.assertIsNone(self.obj.usado_em)

    def test_verificar_ja_usado_retorna_false(self):
        OtpCode.verificar_sms(self.telefone, self.codigo, proposito=OtpCode.PROPOSITO_LOGIN)
        ok, _ = OtpCode.verificar_sms(
            self.telefone, self.codigo, proposito=OtpCode.PROPOSITO_LOGIN,
        )
        self.assertFalse(ok)

    def test_verificar_expirado_retorna_false(self):
        OtpCode.objects.filter(pk=self.obj.pk).update(
            expira_em=timezone.now() - timedelta(seconds=60),
        )
        ok, motivo = OtpCode.verificar_sms(
            self.telefone, self.codigo, proposito=OtpCode.PROPOSITO_LOGIN,
        )
        self.assertFalse(ok)
        self.assertEqual(motivo, 'expirado')

    def test_verificar_telefone_errado_retorna_false(self):
        ok, _ = OtpCode.verificar_sms(
            '17000000000', self.codigo, proposito=OtpCode.PROPOSITO_LOGIN,
        )
        self.assertFalse(ok)

    def test_verificar_proposito_errado_retorna_false(self):
        """OTP de LOGIN nao vale p/ DSAR — challenges sao isolados por proposito."""
        ok, _ = OtpCode.verificar_sms(
            self.telefone, self.codigo, proposito=OtpCode.PROPOSITO_DSAR,
        )
        self.assertFalse(ok)

    def test_nao_permite_duplo_uso(self):
        r1, _ = OtpCode.verificar_sms(self.telefone, self.codigo, proposito=OtpCode.PROPOSITO_LOGIN)
        r2, _ = OtpCode.verificar_sms(self.telefone, self.codigo, proposito=OtpCode.PROPOSITO_LOGIN)
        self.assertTrue(r1)
        self.assertFalse(r2)

    def test_bloqueio_apos_max_tentativas(self):
        for _ in range(OtpCode.MAX_TENTATIVAS):
            OtpCode.verificar_sms(self.telefone, '999999', proposito=OtpCode.PROPOSITO_LOGIN)
        # Mesmo o codigo certo agora falha (bloqueado/consumido)
        ok, _ = OtpCode.verificar_sms(
            self.telefone, self.codigo, proposito=OtpCode.PROPOSITO_LOGIN,
        )
        self.assertFalse(ok)

    def test_gerar_novo_invalida_anterior(self):
        codigo2, _ = OtpCode.gerar_sms(self.telefone, proposito=OtpCode.PROPOSITO_LOGIN)
        ok_antigo, _ = OtpCode.verificar_sms(
            self.telefone, self.codigo, proposito=OtpCode.PROPOSITO_LOGIN,
        )
        self.assertFalse(ok_antigo)
        ok_novo, _ = OtpCode.verificar_sms(
            self.telefone, codigo2, proposito=OtpCode.PROPOSITO_LOGIN,
        )
        self.assertTrue(ok_novo)


class OtpPseudoEmailTests(TestCase):
    def test_email_para_telefone_canonico(self):
        """Formatos diferentes do mesmo numero geram a MESMA chave."""
        a = OtpCode.email_para_telefone('(17) 99999-0001')
        b = OtpCode.email_para_telefone('17999990001')
        self.assertEqual(a, b)
        self.assertEqual(a, 'sms+17999990001@shivazen.local')

    def test_codigo_nunca_em_claro_no_banco(self):
        codigo, obj = OtpCode.gerar_sms('17999990002')
        self.assertNotIn(codigo, obj.codigo_hash)
        self.assertEqual(len(obj.codigo_hash), 64)  # sha256 hex
