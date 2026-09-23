"""Testes de caracterizacao do SlotService (slots livres do dia).

Pinam o comportamento ANTES de extrair a logica do model para o service, para
garantir que a extracao e behavior-preserving (sem regressao de disponibilidade).
"""
from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import (
    Atendimento,
    BloqueioAgenda,
    Cliente,
    DisponibilidadeProfissional,
    ExcecaoDisponibilidade,
    Feriado,
    Habilitacao,
    Procedimento,
    Profissional,
)
from aranha_estetica.services.disponibilidade import SlotService, slot_disponivel


def _dia_semana(d):
    # Mesmo mapeamento usado no SlotService (Dom=1 ... Sab=7).
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
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc)
        self.assertEqual(slots, ['09:00', '09:30', '10:00', '10:30'])

    def test_feriado_retorna_vazio(self):
        Feriado.objects.create(data=self.dia, bloqueia_agendamento=True)
        self.assertEqual(SlotService.slots_livres(self.prof, self.dia, self.proc), [])

    def test_folga_retorna_vazio(self):
        ExcecaoDisponibilidade.objects.create(
            profissional=self.prof, data=self.dia, tipo='FOLGA',
        )
        self.assertEqual(SlotService.slots_livres(self.prof, self.dia, self.proc), [])

    def test_horario_diferente_usa_janela_da_excecao(self):
        ExcecaoDisponibilidade.objects.create(
            profissional=self.prof, data=self.dia, tipo='HORARIO_DIFERENTE',
            hora_inicio=time(14, 0), hora_fim=time(15, 0),
        )
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc)
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
        self.assertEqual(SlotService.slots_livres(self.prof, outro, self.proc), [])

    def test_atendimento_existente_bloqueia_slot(self):
        cli = Cliente.objects.create(nome='Cli', telefone='17900000000')
        inicio = timezone.make_aware(datetime.combine(self.dia, time(9, 0)))
        Atendimento.objects.create(
            profissional=self.prof, cliente=cli, procedimento=self.proc,
            data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=30),
            status='CONFIRMADO',
        )
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc)
        self.assertNotIn('09:00', slots)
        self.assertIn('09:30', slots)

    def test_alem_de_max_advance_retorna_vazio(self):
        longe = (timezone.localtime() + timedelta(days=120)).date()
        self.assertEqual(SlotService.slots_livres(self.prof, longe, self.proc), [])


class SlotServiceCorrecoesTests(TestCase):
    """booking-05/crawl-09: duracao inteira, buffer, bloqueios globais, 2 turnos."""

    def setUp(self):
        self.prof = Profissional.objects.create(
            nome='Dra Slots', ativo=True, min_notice_horas=0, max_advance_dias=60,
        )
        self.proc60 = Procedimento.objects.create(
            nome='Botox', duracao_minutos=60, buffer_minutos=0, ativo=True,
        )
        self.dia = (timezone.localtime() + timedelta(days=7)).date()
        DisponibilidadeProfissional.objects.create(
            profissional=self.prof, dia_semana=_dia_semana(self.dia),
            hora_inicio=time(9, 0), hora_fim=time(12, 0),
        )
        self.cli = Cliente.objects.create(nome='Cli', telefone='17900000009')

    def _at(self, h, m, dur=60, proc=None):
        inicio = timezone.make_aware(datetime.combine(self.dia, time(h, m)))
        return Atendimento.objects.create(
            profissional=self.prof, cliente=self.cli, procedimento=proc or self.proc60,
            data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=dur),
            status='AGENDADO',
        )

    def test_procedimento_precisa_caber_no_expediente(self):
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc60)
        self.assertIn('11:00', slots)
        self.assertNotIn('11:30', slots)  # 11:30 + 60 min passa das 12:00

    def test_sobreposicao_considera_duracao_inteira(self):
        self._at(10, 0)
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc60)
        self.assertNotIn('09:30', slots)  # 09:30-10:30 invade o atendimento das 10h
        self.assertIn('09:00', slots)
        self.assertIn('11:00', slots)

    def test_buffer_do_atendimento_anterior(self):
        proc_buf = Procedimento.objects.create(
            nome='Peeling', duracao_minutos=60, buffer_minutos=30, ativo=True,
        )
        self._at(9, 0, proc=proc_buf)
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc60)
        self.assertNotIn('10:00', slots)  # 10:00-10:30 e o buffer do anterior
        self.assertIn('10:30', slots)

    def test_bloqueio_no_meio_do_slot(self):
        ini = timezone.make_aware(datetime.combine(self.dia, time(10, 15)))
        BloqueioAgenda.objects.create(
            profissional=self.prof, data_hora_inicio=ini, data_hora_fim=ini + timedelta(minutes=30),
        )
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc60)
        self.assertNotIn('09:30', slots)
        self.assertNotIn('10:00', slots)
        self.assertIn('09:00', slots)

    def test_bloqueio_global_vale_para_todos(self):
        ini = timezone.make_aware(datetime.combine(self.dia, time(9, 0)))
        BloqueioAgenda.objects.create(
            profissional=None, data_hora_inicio=ini, data_hora_fim=ini + timedelta(hours=3),
        )
        self.assertEqual(SlotService.slots_livres(self.prof, self.dia, self.proc60), [])

    def test_dois_turnos_no_mesmo_dia(self):
        DisponibilidadeProfissional.objects.create(
            profissional=self.prof, dia_semana=_dia_semana(self.dia),
            hora_inicio=time(14, 0), hora_fim=time(16, 0),
        )
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc60)
        self.assertIn('09:00', slots)
        self.assertIn('14:00', slots)
        self.assertNotIn('13:00', slots)

    def test_ignorar_atendimento_no_reagendamento(self):
        at = self._at(10, 0)
        self.assertNotIn('10:00', SlotService.slots_livres(self.prof, self.dia, self.proc60))
        self.assertIn('10:00', SlotService.slots_livres(
            self.prof, self.dia, self.proc60, ignorar_atendimento_id=at.pk,
        ))

    def test_min_notice_bloqueia_horario_proximo(self):
        self.prof.min_notice_horas = 24 * 30
        self.prof.save()
        self.assertEqual(SlotService.slots_livres(self.prof, self.dia, self.proc60), [])

    def test_slot_disponivel_exige_horario_exato(self):
        ok = timezone.make_aware(datetime.combine(self.dia, time(9, 0)))
        self.assertTrue(slot_disponivel(self.prof, self.proc60, ok))
        self.assertFalse(slot_disponivel(self.prof, self.proc60, ok + timedelta(minutes=7)))
        self.assertFalse(slot_disponivel(
            self.prof, self.proc60, timezone.make_aware(datetime.combine(self.dia, time(3, 0))),
        ))


@override_settings(RATELIMIT_ENABLE=False)
class ApiDisponibilidadeTests(TestCase):
    """Endpoints do wizard usam o SlotService (feriado, folga, max_advance)."""

    def setUp(self):
        cache.clear()
        Feriado.objects.all().delete()
        self.prof = Profissional.objects.create(
            nome='Dra Api', ativo=True, min_notice_horas=0, max_advance_dias=60,
        )
        self.proc = Procedimento.objects.create(
            nome='Limpeza', duracao_minutos=30, buffer_minutos=0, ativo=True,
        )
        Habilitacao.objects.create(profissional=self.prof, procedimento=self.proc)
        for dia in range(1, 8):
            DisponibilidadeProfissional.objects.create(
                profissional=self.prof, dia_semana=dia,
                hora_inicio=time(9, 0), hora_fim=time(11, 0),
            )
        self.dia = (timezone.localtime() + timedelta(days=7)).date()

    def _horarios(self, dia, **extra):
        params = {'data': dia.isoformat(), 'procedimento_id': self.proc.pk}
        params.update(extra)
        return self.client.get(reverse('aranha:api_horarios_disponiveis'), params)

    def test_horarios_normais(self):
        resp = self._horarios(self.dia)
        self.assertEqual(resp.status_code, 200)
        horas = [h['horario'] for h in resp.json()['horarios']]
        self.assertEqual(horas, ['09:00', '09:30', '10:00', '10:30'])
        iso = resp.json()['horarios'][0]['datetime_iso']
        self.assertIn('09:00:00', iso)

    def test_feriado_e_folga_sem_horarios(self):
        Feriado.objects.create(data=self.dia, nome='Feriado', bloqueia_agendamento=True)
        self.assertEqual(self._horarios(self.dia).json()['horarios'], [])
        outro = self.dia + timedelta(days=1)
        ExcecaoDisponibilidade.objects.create(profissional=self.prof, data=outro, tipo='FOLGA')
        self.assertEqual(self._horarios(outro).json()['horarios'], [])

    def test_alem_do_max_advance(self):
        longe = (timezone.localtime() + timedelta(days=200)).date()
        self.assertEqual(self._horarios(longe).json()['horarios'], [])

    def test_dias_exclui_feriado_e_folga(self):
        Feriado.objects.create(data=self.dia, nome='Feriado', bloqueia_agendamento=True)
        folga = self.dia + timedelta(days=1)
        ExcecaoDisponibilidade.objects.create(profissional=self.prof, data=folga, tipo='FOLGA')
        dias = set()
        for d in (self.dia, folga, self.dia + timedelta(days=2)):
            resp = self.client.get(reverse('aranha:api_dias_disponiveis'), {
                'mes': d.strftime('%Y-%m'), 'procedimento_id': self.proc.pk,
            })
            self.assertEqual(resp.status_code, 200)
            dias.update(resp.json()['dias_disponiveis'])
        self.assertNotIn(self.dia.isoformat(), dias)
        self.assertNotIn(folga.isoformat(), dias)
        self.assertIn((self.dia + timedelta(days=2)).isoformat(), dias)

    def test_parametros_invalidos_retornam_400(self):
        for params in (
            {'data': self.dia.isoformat(), 'procedimento_id': 'abc'},
            {'data': 'lixo', 'procedimento_id': self.proc.pk},
        ):
            resp = self.client.get(reverse('aranha:api_horarios_disponiveis'), params)
            self.assertEqual(resp.status_code, 400)
        resp = self.client.get(reverse('aranha:api_dias_disponiveis'), {
            'mes': '2026-13', 'procedimento_id': self.proc.pk,
        })
        self.assertEqual(resp.status_code, 400)
        resp = self.client.get(reverse('aranha:api_dias_disponiveis'), {
            'mes': '2026-10', 'procedimento_id': 'x1',
        })
        self.assertEqual(resp.status_code, 400)

    def test_filtro_por_profissional_preselecionado(self):
        outra = Profissional.objects.create(nome='Dra Outra', ativo=True, min_notice_horas=0)
        Habilitacao.objects.create(profissional=outra, procedimento=self.proc)
        DisponibilidadeProfissional.objects.create(
            profissional=outra, dia_semana=_dia_semana(self.dia),
            hora_inicio=time(9, 0), hora_fim=time(10, 0),
        )
        todos = self._horarios(self.dia).json()['horarios'][0]['profissionais']
        self.assertEqual(len(todos), 2)
        so_outra = self._horarios(self.dia, profissional_id=outra.pk).json()['horarios']
        self.assertTrue(so_outra)
        self.assertTrue(all(
            [p['id'] for p in h['profissionais']] == [outra.pk] for h in so_outra
        ))
