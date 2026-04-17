"""Testes para OTP (model + service + fluxo booking/meus-agendamentos)."""
import re
from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from app_shivazen.models import OtpCode
from app_shivazen.services import otp_service


@pytest.mark.django_db
class TestOtpCodeModel:
    def test_gerar_e_verificar_ok(self):
        codigo, obj = OtpCode.gerar('joao@example.com')
        assert re.fullmatch(r'\d{6}', codigo)
        assert obj.codigo_hash != codigo  # guardado hashed
        ok, motivo = OtpCode.verificar('joao@example.com', codigo)
        assert ok and motivo == 'ok'

    def test_codigo_errado_incrementa_tentativas(self):
        codigo, _ = OtpCode.gerar('a@x.com')
        ok, motivo = OtpCode.verificar('a@x.com', '000000' if codigo != '000000' else '111111')
        assert not ok
        assert motivo.startswith('incorreto')

    def test_bloqueio_apos_max_tentativas(self):
        OtpCode.gerar('z@x.com')
        for _ in range(OtpCode.MAX_TENTATIVAS):
            OtpCode.verificar('z@x.com', '000000')
        ok, motivo = OtpCode.verificar('z@x.com', '000000')
        assert not ok
        # apos consumir com max, codigo fica usado — retorna expirado
        assert motivo in ('bloqueado', 'expirado')

    def test_expirado(self):
        codigo, obj = OtpCode.gerar('exp@x.com')
        obj.expira_em = timezone.now() - timedelta(seconds=1)
        obj.save()
        ok, motivo = OtpCode.verificar('exp@x.com', codigo)
        assert not ok and motivo == 'expirado'

    def test_reenvio_invalida_anteriores(self):
        codigo1, _ = OtpCode.gerar('re@x.com')
        # burla rate limit via save direto
        OtpCode.objects.filter(email='re@x.com').update(
            criado_em=timezone.now() - timedelta(seconds=120)
        )
        codigo2, _ = OtpCode.gerar('re@x.com')
        ok1, _ = OtpCode.verificar('re@x.com', codigo1)
        assert not ok1
        ok2, _ = OtpCode.verificar('re@x.com', codigo2)
        assert ok2


@pytest.mark.django_db
class TestOtpService:
    def test_solicitar_envia_email(self):
        mail.outbox.clear()
        ok, motivo = otp_service.solicitar_otp('foo@example.com')
        assert ok and motivo == 'ok'
        assert len(mail.outbox) == 1
        assert 'foo@example.com' in mail.outbox[0].to

    def test_rate_limit_reenvio(self):
        otp_service.solicitar_otp('rl@example.com')
        ok, motivo = otp_service.solicitar_otp('rl@example.com')
        assert not ok and motivo == 'aguarde'

    def test_verificar_email_vazio(self):
        ok, motivo = otp_service.verificar_otp('', '123456')
        assert not ok and motivo == 'dados_ausentes'
