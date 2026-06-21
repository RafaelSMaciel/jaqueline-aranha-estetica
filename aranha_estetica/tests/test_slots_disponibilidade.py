"""Testes de caracterizacao de get_horarios_disponiveis / SlotService.

Pinam o comportamento ANTES de extrair a logica do model para o service, para
garantir que a extracao e behavior-preserving (sem regressao de disponibilidade).
"""
from datetime import datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import (
    Atendimento,
    Cliente,
    DisponibilidadeProfissional,
    ExcecaoDisponibilidade,
    Feriado,
    Procedimento,
    Profissional,
)


def _dia_semana(d):
    # Mesmo mapeamento usado em get_horarios_disponiveis (Dom=1 ... Sab=7).
    return d.isoweekday() % 7 + 1


class SlotsDisponibilidadeTests(TestCase):
    def setUp(self):
        self.prof = Profissional.objects.create(
            nome='Dra Teste', ativo=True, min_notice_horas=0, max_advance_dias=60,
        )
        self.proc = Procedimento.objects.create(
            nome='Limpeza', duracao_minutos=30, buffer_minutos=0, ativo=True,
        )
        self.dia = (timezone.localtime() + timedelta(days=7)).date()
        DisponibilidadeProfissional.objects.create(
            profissional=self.prof, dia_semana=_dia_semana(self.dia),
            hora_inicio=time(9, 0), hora_fim=time(11, 0),
        )

    def test_dia_normal_gera_slots_30min(self):
        slots = self.prof.get_horarios_disponiveis(self.dia, self.proc)
        self.assertEqual(slots, ['09:00', '09:30', '10:00', '10:30'])

    def test_feriado_retorna_vazio(self):
        Feriado.objects.create(data=self.dia, bloqueia_agendamento=True)
        self.assertEqual(self.prof.get_horarios_disponiveis(self.dia, self.proc), [])

    def test_folga_retorna_vazio(self):
        ExcecaoDisponibilidade.objects.create(
            profissional=self.prof, data=self.dia, tipo='FOLGA',
        )
        self.assertEqual(self.prof.get_horarios_disponiveis(self.dia, self.proc), [])

    def test_horario_diferente_usa_janela_da_excecao(self):
        ExcecaoDisponibilidade.objects.create(
            profissional=self.prof, data=self.dia, tipo='HORARIO_DIFERENTE',
            hora_inicio=time(14, 0), hora_fim=time(15, 0),
        )
        slots = self.prof.get_horarios_disponiveis(self.dia, self.proc)
        self.assertEqual(slots, ['14:00', '14:30'])

    def test_sem_disponibilidade_no_dia(self):
        # 'outro' cai em outro dia da semana (dia+1), sem DisponibilidadeProfissional.
        outro = self.dia + timedelta(days=1)
        # Precondicao explicita: se a fixture mudar e passar a ter disponibilidade
        # nesse dia, o teste FALHA aqui em vez de passar sem exercitar o assert.
        self.assertFalse(
            DisponibilidadeProfissional.objects.filter(
                profissional=self.prof, dia_semana=_dia_semana(outro)
            ).exists()
        )
        self.assertEqual(self.prof.get_horarios_disponiveis(outro, self.proc), [])

    def test_atendimento_existente_bloqueia_slot(self):
        cli = Cliente.objects.create(nome='Cli', telefone='17900000000')
        inicio = timezone.make_aware(datetime.combine(self.dia, time(9, 0)))
        Atendimento.objects.create(
            profissional=self.prof, cliente=cli, procedimento=self.proc,
            data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=30),
            status='CONFIRMADO',
        )
        slots = self.prof.get_horarios_disponiveis(self.dia, self.proc)
        self.assertNotIn('09:00', slots)
        self.assertIn('09:30', slots)

    def test_alem_de_max_advance_retorna_vazio(self):
        longe = (timezone.localtime() + timedelta(days=120)).date()
        self.assertEqual(self.prof.get_horarios_disponiveis(longe, self.proc), [])
