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
    AgendaExcecao,
    AgendaHorario,
    Atendimento,
    Cliente,
    Habilitacao,
    Procedimento,
    Profissional,
)
from aranha_estetica.services.disponibilidade import SlotService, slot_disponivel

from .factories import limpar_feriados


def _dia_semana(d):
    # Mesmo mapeamento usado no SlotService (ISO: seg=1 ... dom=7).
    return d.isoweekday()


def _feriado(dia, ativo=True, nome='Feriado'):
    """Feriado = FOLGA da clinica inteira (profissional NULL)."""
    return AgendaExcecao.objects.create(
        tipo='FOLGA', profissional=None, origem='FERIADO_LOCAL', data_inicio=dia, data_fim=dia,
        motivo=nome, ativo=ativo,
    )


def _folga(prof, dia, ate=None):
    return AgendaExcecao.objects.create(
        tipo='FOLGA', profissional=prof, data_inicio=dia, data_fim=ate or dia,
    )


def _bloqueio(prof, inicio, fim, **kw):
    return AgendaExcecao.objects.create(
        tipo='BLOQUEIO', profissional=prof, data_hora_inicio=inicio, data_hora_fim=fim, **kw,
    )


class SlotsDisponibilidadeTests(TestCase):
    def setUp(self):
        self.prof = Profissional.objects.create(
            nome='Dra Teste', ativo=True, min_notice_horas=0, max_advance_dias=60,
        )
        self.proc = Procedimento.objects.create(
            nome='Limpeza', duracao_minutos=30, buffer_minutos=0, ativo=True,
        )
        self.dia = (timezone.localtime() + timedelta(days=7)).date()
        limpar_feriados()
        AgendaHorario.objects.create(
            profissional=self.prof, dia_semana=_dia_semana(self.dia),
            hora_inicio=time(9, 0), hora_fim=time(11, 0),
        )

    def test_dia_normal_gera_slots_30min(self):
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc)
        self.assertEqual(slots, ['09:00', '09:30', '10:00', '10:30'])

    def test_feriado_retorna_vazio(self):
        _feriado(self.dia)
        self.assertEqual(SlotService.slots_livres(self.prof, self.dia, self.proc), [])

    def test_feriado_inativo_nao_bloqueia(self):
        # feriado nacional em que a clinica abre (ativo=False: registrado, mas ignorado)
        _feriado(self.dia, ativo=False)
        self.assertEqual(SlotService.slots_livres(self.prof, self.dia, self.proc),
                         ['09:00', '09:30', '10:00', '10:30'])

    def test_folga_retorna_vazio(self):
        _folga(self.prof, self.dia)
        self.assertEqual(SlotService.slots_livres(self.prof, self.dia, self.proc), [])

    def test_folga_em_intervalo_cobre_o_meio(self):
        # ferias numa linha so: o dia do meio do intervalo tambem fica sem horario
        _folga(self.prof, self.dia - timedelta(days=2), ate=self.dia + timedelta(days=2))
        self.assertEqual(SlotService.slots_livres(self.prof, self.dia, self.proc), [])

    def test_folga_de_outro_profissional_nao_afeta(self):
        outra = Profissional.objects.create(nome='Outra', ativo=True)
        _folga(outra, self.dia)
        self.assertEqual(len(SlotService.slots_livres(self.prof, self.dia, self.proc)), 4)

    def test_horario_diferente_usa_janela_da_excecao(self):
        AgendaExcecao.objects.create(
            profissional=self.prof, data_inicio=self.dia, data_fim=self.dia, tipo='HORARIO_DIFERENTE',
            hora_inicio=time(14, 0), hora_fim=time(15, 0),
        )
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc)
        self.assertEqual(slots, ['14:00', '14:30'])

    def test_sem_disponibilidade_no_dia(self):
        # 'outro' cai em outro dia da semana (dia+1), sem janela na grade (AgendaHorario).
        outro = self.dia + timedelta(days=1)
        # Precondicao explicita: se a fixture mudar e passar a ter disponibilidade
        # nesse dia, o teste FALHA aqui em vez de passar sem exercitar o assert.
        self.assertFalse(
            AgendaHorario.objects.filter(
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
        limpar_feriados()
        AgendaHorario.objects.create(
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
        _bloqueio(self.prof, ini, ini + timedelta(minutes=30))
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc60)
        self.assertNotIn('09:30', slots)
        self.assertNotIn('10:00', slots)
        self.assertIn('09:00', slots)

    def test_bloqueio_global_vale_para_todos(self):
        ini = timezone.make_aware(datetime.combine(self.dia, time(9, 0)))
        _bloqueio(None, ini, ini + timedelta(hours=3))
        self.assertEqual(SlotService.slots_livres(self.prof, self.dia, self.proc60), [])

    def test_bloqueio_inativo_ignorado(self):
        ini = timezone.make_aware(datetime.combine(self.dia, time(9, 0)))
        _bloqueio(None, ini, ini + timedelta(hours=3), ativo=False)
        self.assertIn('09:00', SlotService.slots_livres(self.prof, self.dia, self.proc60))

    def test_bloqueio_recorrente_semanal_respeita_fim_inclusivo(self):
        # comecou 2 semanas antes, toda semana no mesmo dia 10:00-11:00
        ini = timezone.make_aware(datetime.combine(self.dia - timedelta(days=14), time(10, 0)))
        rec = _bloqueio(self.prof, ini, ini + timedelta(hours=1),
                        regra_recorrencia='FREQ=WEEKLY', data_fim_recorrencia=self.dia)
        slots = SlotService.slots_livres(self.prof, self.dia, self.proc60)
        self.assertEqual(slots, ['09:00', '11:00'])  # 09:30 invade 10h; 10:00/10:30 no bloqueio
        rec.data_fim_recorrencia = self.dia - timedelta(days=1)
        rec.save()
        self.assertEqual(SlotService.slots_livres(self.prof, self.dia, self.proc60),
                         ['09:00', '09:30', '10:00', '10:30', '11:00'])

    def test_dois_turnos_no_mesmo_dia(self):
        AgendaHorario.objects.create(
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
        limpar_feriados()
        self.prof = Profissional.objects.create(
            nome='Dra Api', ativo=True, min_notice_horas=0, max_advance_dias=60,
        )
        self.proc = Procedimento.objects.create(
            nome='Limpeza', duracao_minutos=30, buffer_minutos=0, ativo=True,
        )
        Habilitacao.objects.create(profissional=self.prof, procedimento=self.proc)
        for dia in range(1, 8):
            AgendaHorario.objects.create(
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
        _feriado(self.dia)
        self.assertEqual(self._horarios(self.dia).json()['horarios'], [])
        outro = self.dia + timedelta(days=1)
        _folga(self.prof, outro)
        self.assertEqual(self._horarios(outro).json()['horarios'], [])

    def test_alem_do_max_advance(self):
        longe = (timezone.localtime() + timedelta(days=200)).date()
        self.assertEqual(self._horarios(longe).json()['horarios'], [])

    def test_dias_exclui_feriado_e_folga(self):
        _feriado(self.dia)
        folga = self.dia + timedelta(days=1)
        _folga(self.prof, folga)
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

    def _dias(self, dia):
        resp = self.client.get(reverse('aranha:api_dias_disponiveis'), {
            'mes': dia.strftime('%Y-%m'), 'procedimento_id': self.proc.pk,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.json()['ok'], True)
        return set(resp.json()['dias_disponiveis'])

    def test_dias_exclui_dia_com_expediente_inteiro_bloqueado(self):
        """Bloqueio que cobre o expediente inteiro (inclusive em pedacos ou recorrente) tira o dia
        do calendario; bloqueio parcial nao."""
        def aware(dia, h, m=0):
            return timezone.make_aware(datetime.combine(dia, time(h, m)))
        inteiro, pedacos, parcial = self.dia, self.dia + timedelta(days=1), self.dia + timedelta(days=2)
        _bloqueio(self.prof, aware(inteiro, 8), aware(inteiro, 12))
        _bloqueio(None, aware(pedacos, 9), aware(pedacos, 10))   # da clinica inteira
        _bloqueio(self.prof, aware(pedacos, 10), aware(pedacos, 11))
        _bloqueio(self.prof, aware(parcial, 9), aware(parcial, 10))
        dias = set()
        for dia in (inteiro, pedacos, parcial):
            dias |= self._dias(dia)
        self.assertNotIn(inteiro.isoformat(), dias)
        self.assertNotIn(pedacos.isoformat(), dias)
        self.assertIn(parcial.isoformat(), dias)

    def test_dias_exclui_bloqueio_recorrente_do_expediente(self):
        def aware(dia, h):
            return timezone.make_aware(datetime.combine(dia, time(h)))
        _bloqueio(self.prof, aware(self.dia, 9), aware(self.dia, 11), regra_recorrencia='FREQ=WEEKLY')
        semana_que_vem = self.dia + timedelta(days=7)
        dias = self._dias(self.dia) | self._dias(semana_que_vem)
        self.assertNotIn(self.dia.isoformat(), dias)
        self.assertNotIn(semana_que_vem.isoformat(), dias)
        self.assertIn((self.dia + timedelta(days=1)).isoformat(), dias)

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
        AgendaHorario.objects.create(
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


class HabilitacaoObrigatoriaTests(TestCase):
    """So atende quem esta habilitado: sem habilitacao o procedimento some do agendamento."""

    def setUp(self):
        from .factories import criar_procedimento, criar_profissional
        self.ana = criar_profissional('Ana')
        self.bia = criar_profissional('Bia')
        self.toxina = criar_procedimento('Toxina', profissional=self.ana)
        self.sem_ninguem = criar_procedimento('Novo protocolo')

    def test_profissionais_para_so_habilitados(self):
        from aranha_estetica.services.disponibilidade import procedimentos_agendaveis, profissionais_para
        self.assertEqual(list(profissionais_para(self.toxina)), [self.ana])
        self.assertEqual(list(profissionais_para(self.sem_ninguem)), [])
        self.assertIn(self.toxina, procedimentos_agendaveis())
        self.assertNotIn(self.sem_ninguem, procedimentos_agendaveis())
        self.ana.ativo = False
        self.ana.save(update_fields=['ativo'])
        self.assertNotIn(self.toxina, procedimentos_agendaveis())

    def test_wizard_nao_oferece_procedimento_sem_habilitado(self):
        html = self.client.get(reverse('aranha:agendamento_publico')).content.decode()
        self.assertIn('Toxina', html)
        self.assertNotIn('Novo protocolo', html)
