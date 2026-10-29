"""utils/datas: "hoje" e exibicao no fuso da clinica; leitura de data/hora de formulario."""
from datetime import date, datetime, time
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from aranha_estetica.utils import datas

# 22h30 em Sao Paulo = 01h30 do dia seguinte em UTC
AGORA_UTC = datetime(2026, 10, 29, 1, 30, tzinfo=dt_timezone.utc)


class FusoDaClinicaTests(SimpleTestCase):
    def test_hoje_e_o_dia_local_mesmo_depois_das_21h(self):
        with patch('django.utils.timezone.now', return_value=AGORA_UTC):
            self.assertEqual(datas.hoje(), date(2026, 10, 28))

    def test_exibicao_no_fuso_local(self):
        self.assertEqual(datas.fmt_local(AGORA_UTC), '28/10/2026 22:30')
        self.assertEqual(datas.data_local(AGORA_UTC), date(2026, 10, 28))
        self.assertEqual(datas.fmt_local(None), '')
        self.assertIsNone(datas.data_local(None))
        self.assertIsNone(datas.local(None))
        naive = datetime(2026, 10, 28, 9, 0)  # noqa: DTZ001 - naive volta como esta
        self.assertIs(datas.local(naive), naive)


class LeituraDeFormularioTests(SimpleTestCase):
    def test_ler_data(self):
        self.assertEqual(datas.ler_data('2026-10-28'), date(2026, 10, 28))
        for invalido in ('', '28/10/2026', '2026-02-30', 'amanha'):
            with self.subTest(valor=invalido), self.assertRaises(ValueError):
                datas.ler_data(invalido)

    def test_ler_hora(self):
        self.assertEqual(datas.ler_hora('09:30'), time(9, 30))
        self.assertEqual(datas.ler_hora('21:05:10'), time(21, 5, 10))
        for invalido in ('', '25:00', '9h30', '09:30:00:00'):
            with self.subTest(valor=invalido), self.assertRaises(ValueError):
                datas.ler_hora(invalido)

    def test_ler_instante_iso(self):
        # 'Z' do toISOString do navegador vira o horario local; sem offset = fuso da clinica
        instante = datas.ler_instante_iso('2026-10-29T01:30:00.000Z')
        self.assertEqual(instante, AGORA_UTC)
        self.assertEqual((instante.date(), instante.hour), (date(2026, 10, 28), 22))
        sem_fuso = datas.ler_instante_iso('2026-10-28T22:30:00')
        self.assertEqual(sem_fuso, AGORA_UTC)
        self.assertEqual(datas.ler_instante_iso('2026-10-28'), datas.inicio_do_dia(date(2026, 10, 28)))
        for invalido in ('', 'amanha', '2026-13-01T10:00', None, 5, ['2026-10-28']):
            with self.subTest(valor=invalido), self.assertRaises(ValueError):
                datas.ler_instante_iso(invalido)


class DiasLocaisTests(TestCase):
    """Filtro por intervalo do dia local: mesmo resultado do __date, sem perder o indice."""

    def test_atendimento_das_23h30_fica_no_dia_local(self):
        from django.utils import timezone

        from aranha_estetica.models import Atendimento

        from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        noite = timezone.make_aware(datetime(2030, 3, 6, 23, 30))   # 02h30 do dia 7 em UTC
        at = criar_atendimento(criar_cliente(), prof, proc, data_hora=noite)
        filtro = lambda de, ate=None: set(  # noqa: E731
            Atendimento.objects.filter(**datas.dias_locais('data_hora_inicio', de, ate)).values_list('pk', flat=True))
        self.assertEqual(filtro(date(2030, 3, 6)), {at.pk})
        self.assertEqual(filtro(date(2030, 3, 7)), set())
        self.assertEqual(filtro(date(2030, 3, 1), date(2030, 3, 6)), {at.pk})   # ate inclusivo
        self.assertEqual(filtro(date(2030, 3, 6)),
                         set(Atendimento.objects.filter(data_hora_inicio__date=date(2030, 3, 6))
                             .values_list('pk', flat=True)))
