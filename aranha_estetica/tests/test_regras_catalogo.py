"""Catalogo de regras (aranha_estetica/regras.py): fonte da tabela "Valores-chave" do
docs/REGRAS-DE-NEGOCIO.md — o documento nao pode ficar para tras do codigo."""
from pathlib import Path

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from aranha_estetica import regras
from aranha_estetica.models.configuracao import CHAVES

DOC = Path(settings.BASE_DIR) / 'docs' / 'REGRAS-DE-NEGOCIO.md'


class CatalogoDeRegrasTests(SimpleTestCase):
    def test_documento_em_dia_com_o_catalogo(self):
        texto = DOC.read_text(encoding='utf-8')
        self.assertEqual(regras.aplicar_no_documento(texto), texto,
                         'rode `python manage.py gerar_docs_regras` e faca commit do REGRAS-DE-NEGOCIO.md')

    def test_toda_regra_tem_valor(self):
        for regra in regras.CATALOGO:
            with self.subTest(regra=regra.rotulo):
                self.assertTrue(regra.valor().strip())
                self.assertTrue(regra.onde.strip())

    def test_toda_regra_operacional_do_painel_esta_no_catalogo(self):
        operacionais = ' '.join(r.onde for r in regras.CATALOGO if r.operacional)
        for chave in CHAVES:
            with self.subTest(chave=chave):
                self.assertIn(f'`{chave}`', operacionais)

    @override_settings(OTP_TTL_SEGUNDOS=60, SMS_MAX_POR_HORA=99)
    def test_documenta_o_padrao_da_env_nao_o_valor_do_ambiente(self):
        tabela = regras.tabela_markdown()
        self.assertIn('| 10 min / 5 / 60 s |', tabela)
        self.assertIn('| 3 / 10 / 60 |', tabela)

    def test_faixa_vem_do_check_do_banco(self):
        from aranha_estetica.models import Profissional

        self.assertEqual(regras._faixa(Profissional, 'min_notice_horas'), (0, 720))
        self.assertEqual(regras._faixa(Profissional, 'max_advance_dias'), (1, 365))

    def test_formatacao(self):
        from datetime import timedelta
        from decimal import Decimal

        self.assertEqual(regras._duracao(timedelta(hours=24)), '24 h')
        self.assertEqual(regras._duracao(timedelta(days=7)), '7 dias')
        self.assertEqual(regras._duracao(timedelta(minutes=30)), '30 min')
        self.assertEqual(regras._dias(365 * 20), '20 anos')
        self.assertEqual(regras._reais(Decimal('1250.5')), 'R$ 1.250,50')

    def test_comando_check_reprova_documento_atrasado(self):
        from unittest.mock import patch

        from aranha_estetica.management.commands import gerar_docs_regras

        with patch.object(gerar_docs_regras.regras, 'tabela_markdown', return_value='| x |'):
            with self.assertRaises(CommandError):
                call_command('gerar_docs_regras', '--check')
        call_command('gerar_docs_regras', '--check')   # o documento real esta em dia
