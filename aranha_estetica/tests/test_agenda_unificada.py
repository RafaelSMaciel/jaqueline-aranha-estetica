"""Agenda unificada (0048): AgendaHorario + AgendaExcecao e quem as consome.

RRULE no subconjunto (utils.recorrencia), validacoes do model (espelho dos
CHECK/EXCLUDE do PG), dias disponiveis com folga em intervalo e feriado
inativo, painel de excecoes, calendario/mover com recorrencia expandida
e carregar_feriados.
"""
import json
from datetime import date, datetime, time, timedelta
from io import StringIO

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import (
    AgendaExcecao, AgendaHorario, Habilitacao, Procedimento, Profissional, Usuario,
)
from aranha_estetica.utils.recorrencia import (
    REGEX_SUBCONJUNTO, RegraNaoSuportada, normalizar_regra_recorrencia,
)

from .factories import (
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional, limpar_feriados,
)


def _local(dia, hora, minuto=0):
    return timezone.make_aware(datetime.combine(dia, time(hora, minuto)))


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class NormalizarRegraTests(SimpleTestCase):
    def test_forma_canonica(self):
        casos = {
            'FREQ=WEEKLY;BYDAY=WE': 'FREQ=WEEKLY;BYDAY=WE',
            'rrule:freq=weekly;byday=fr,mo,fr;wkst=MO;': 'FREQ=WEEKLY;BYDAY=MO,FR',
            'FREQ=DAILY;INTERVAL=1': 'FREQ=DAILY',
            'FREQ=DAILY;INTERVAL=2;BYDAY=MO,TU': 'FREQ=DAILY;INTERVAL=2;BYDAY=MO,TU',
            'FREQ=WEEKLY;BYDAY=1MO': 'FREQ=WEEKLY;BYDAY=MO',  # ordinal fora de MONTHLY: dateutil ignorava
            'FREQ=MONTHLY;BYDAY=+1SA': 'FREQ=MONTHLY;BYDAY=1SA',
            'FREQ=MONTHLY;BYDAY=-1FR,2TU': 'FREQ=MONTHLY;BYDAY=2TU,-1FR',
            'FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=31': 'FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=31',
            'FREQ=MONTHLY': 'FREQ=MONTHLY',
        }
        import re
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                regra, limite = normalizar_regra_recorrencia(entrada)
                self.assertEqual((regra, limite), (esperado, None))
                self.assertTrue(re.fullmatch(REGEX_SUBCONJUNTO, regra))

    def test_vazio_e_none(self):
        for vazio in (None, '', '   '):
            self.assertEqual(normalizar_regra_recorrencia(vazio), (None, None))

    def test_fora_do_subconjunto(self):
        for regra in ('FREQ=YEARLY', 'FREQ=HOURLY', 'FREQ=WEEKLY;BYHOUR=10', 'FREQ=MONTHLY;BYDAY=5FR',
                      'FREQ=MONTHLY;BYDAY=-2FR', 'FREQ=WEEKLY;BYMONTHDAY=3', 'FREQ=MONTHLY;BYMONTHDAY=1,15',
                      'FREQ=MONTHLY;BYDAY=MO;BYMONTHDAY=1', 'FREQ=DAILY;INTERVAL=0', 'FREQ=DAILY;INTERVAL=100',
                      'FREQ=WEEKLY;WKST=SU', 'FREQ=DAILY;COUNT=2;UNTIL=20300101', 'FREQ=DAILY;FREQ=WEEKLY',
                      'BYDAY=MO', 'lixo', 'FREQ=WEEKLY;BYDAY=XX', 'FREQ=DAILY\nEXDATE:20300101'):
            with self.subTest(regra=regra), self.assertRaises(RegraNaoSuportada):
                normalizar_regra_recorrencia(regra, dtstart=datetime(2030, 1, 1, 10))

    def test_count_e_until_viram_data(self):
        inicio = datetime(2030, 1, 1, 10)  # terca
        self.assertEqual(normalizar_regra_recorrencia('FREQ=DAILY;COUNT=3', dtstart=inicio),
                         ('FREQ=DAILY', date(2030, 1, 3)))
        self.assertEqual(normalizar_regra_recorrencia('FREQ=WEEKLY;BYDAY=MO,WE;COUNT=3', dtstart=inicio),
                         ('FREQ=WEEKLY;BYDAY=MO,WE', date(2030, 1, 9)))  # DTSTART (ter) nao casa: qua, seg, qua
        # UNTIL em data: o dia inteiro vale; UNTIL antes da hora do DTSTART exclui o dia
        self.assertEqual(normalizar_regra_recorrencia('FREQ=DAILY;UNTIL=20300105', dtstart=inicio),
                         ('FREQ=DAILY', date(2030, 1, 5)))
        self.assertEqual(normalizar_regra_recorrencia('FREQ=DAILY;UNTIL=20300105T090000', dtstart=inicio),
                         ('FREQ=DAILY', date(2030, 1, 4)))
        with self.assertRaises(RegraNaoSuportada):
            normalizar_regra_recorrencia('FREQ=DAILY;COUNT=3')  # sem DTSTART nao da p/ converter


class AgendaExcecaoModelTests(TestCase):
    def setUp(self):
        self.prof = Profissional.objects.create(nome='Dra Model')
        self.dia = date(2030, 6, 10)

    def _clean(self, **kw):
        e = AgendaExcecao(**kw)
        e.full_clean()
        return e

    def test_folga_de_um_dia_completa_data_fim(self):
        e = self._clean(tipo='FOLGA', profissional=self.prof, data_inicio=self.dia)
        self.assertEqual(e.data_fim, self.dia)

    def test_folga_sobreposta_do_mesmo_profissional_recusada(self):
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=self.prof, data_inicio=self.dia,
                                     data_fim=self.dia + timedelta(days=4))
        with self.assertRaises(ValidationError):
            self._clean(tipo='FOLGA', profissional=self.prof, data_inicio=self.dia + timedelta(days=4))
        # inativa nao conta; outro profissional e global tambem nao
        self._clean(tipo='FOLGA', profissional=self.prof, data_inicio=self.dia + timedelta(days=5))
        self._clean(tipo='FOLGA', profissional=None, origem='RECESSO', data_inicio=self.dia)

    def test_horario_diferente_exige_profissional_e_horas_validas(self):
        with self.assertRaises(ValidationError):
            self._clean(tipo='HORARIO_DIFERENTE', data_inicio=self.dia, hora_inicio=time(9), hora_fim=time(12))
        with self.assertRaises(ValidationError):
            self._clean(tipo='HORARIO_DIFERENTE', profissional=self.prof, data_inicio=self.dia,
                        hora_inicio=time(12), hora_fim=time(9))
        e = self._clean(tipo='HORARIO_DIFERENTE', profissional=self.prof, data_inicio=self.dia,
                        data_fim=self.dia + timedelta(days=3), hora_inicio=time(9), hora_fim=time(12))
        self.assertEqual(e.data_fim, self.dia)  # vale um dia

    def test_feriado_coerente_e_origem_gcal_nao_existe_mais(self):
        with self.assertRaises(ValidationError):
            self._clean(tipo='FOLGA', profissional=self.prof, origem='FERIADO_NACIONAL', data_inicio=self.dia)
        # Google Agenda removido (0068): GCAL deixou de ser origem valida
        with self.assertRaises(ValidationError) as ctx:
            self._clean(tipo='BLOQUEIO', profissional=self.prof, origem='GCAL',
                        data_hora_inicio=_local(self.dia, 9), data_hora_fim=_local(self.dia, 10))
        self.assertIn('origem', ctx.exception.message_dict)

    def test_bloqueio_normaliza_rrule_e_converte_count(self):
        e = self._clean(tipo='BLOQUEIO', profissional=self.prof,
                        data_hora_inicio=_local(self.dia, 9), data_hora_fim=_local(self.dia, 10),
                        regra_recorrencia=' RRULE:FREQ=DAILY;COUNT=5 ')
        self.assertEqual((e.regra_recorrencia, e.data_fim_recorrencia), ('FREQ=DAILY', self.dia + timedelta(days=4)))
        with self.assertRaises(ValidationError):
            self._clean(tipo='BLOQUEIO', profissional=self.prof, regra_recorrencia='FREQ=YEARLY',
                        data_hora_inicio=_local(self.dia, 9), data_hora_fim=_local(self.dia, 10))
        with self.assertRaises(ValidationError):  # fim de recorrencia sem regra
            self._clean(tipo='BLOQUEIO', profissional=self.prof, data_fim_recorrencia=self.dia,
                        data_hora_inicio=_local(self.dia, 9), data_hora_fim=_local(self.dia, 10))

    def test_ocorrencias_folga_e_bloqueio_pontual(self):
        folga = AgendaExcecao(tipo='FOLGA', data_inicio=self.dia, data_fim=self.dia + timedelta(days=1))
        ini, fim = _local(self.dia, 0), _local(self.dia + timedelta(days=3), 0)
        self.assertEqual(folga.ocorrencias(ini, fim), [(ini, _local(self.dia + timedelta(days=2), 0))])
        pontual = AgendaExcecao(tipo='BLOQUEIO', data_hora_inicio=_local(self.dia, 9),
                                data_hora_fim=_local(self.dia, 10))
        self.assertEqual(len(pontual.ocorrencias(ini, fim)), 1)
        self.assertEqual(pontual.ocorrencias(fim, fim + timedelta(days=1)), [])

    def test_folga_global_em(self):
        AgendaExcecao.objects.create(tipo='FOLGA', origem='FERIADO_LOCAL', data_inicio=self.dia, data_fim=self.dia)
        AgendaExcecao.objects.create(tipo='FOLGA', origem='FERIADO_NACIONAL', ativo=False,
                                     data_inicio=self.dia + timedelta(days=1), data_fim=self.dia + timedelta(days=1))
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=self.prof, data_inicio=self.dia + timedelta(days=2),
                                     data_fim=self.dia + timedelta(days=2))
        folga = AgendaExcecao.objects.folga_global_em
        self.assertTrue(folga(self.dia).exists())
        self.assertFalse(folga(self.dia + timedelta(days=1)).exists())  # inativo: clinica abre
        self.assertFalse(folga(self.dia + timedelta(days=2)).exists())  # folga de profissional

    def test_grade_sem_sobreposicao(self):
        AgendaHorario.objects.create(profissional=self.prof, dia_semana=1, hora_inicio=time(9), hora_fim=time(12))
        with self.assertRaises(ValidationError):
            AgendaHorario(profissional=self.prof, dia_semana=1, hora_inicio=time(11), hora_fim=time(13)).full_clean()
        AgendaHorario(profissional=self.prof, dia_semana=1, hora_inicio=time(12), hora_fim=time(13)).full_clean()


@override_settings(RATELIMIT_ENABLE=False)
class DiasDisponiveisTests(TestCase):
    def setUp(self):
        limpar_feriados()
        self.prof = Profissional.objects.create(nome='Dra Dias', ativo=True, min_notice_horas=0,
                                                max_advance_dias=90)
        self.proc = Procedimento.objects.create(nome='Limpeza', duracao_minutos=30, ativo=True)
        Habilitacao.objects.create(profissional=self.prof, procedimento=self.proc)
        # proximo mes inteiro: sem "hoje" no meio
        hoje = timezone.localdate()
        self.inicio = (hoje.replace(day=1) + timedelta(days=40)).replace(day=1)
        for dia in range(1, 6):  # seg-sex
            AgendaHorario.objects.create(profissional=self.prof, dia_semana=dia,
                                         hora_inicio=time(9), hora_fim=time(12))

    def _dias(self):
        resp = self.client.get(reverse('aranha:api_dias_disponiveis'), {
            'mes': self.inicio.strftime('%Y-%m'), 'procedimento_id': self.proc.pk,
        })
        self.assertEqual(resp.status_code, 200)
        return set(resp.json()['dias_disponiveis'])

    def _uteis(self, n):
        d = self.inicio
        uteis = []
        while len(uteis) < n:
            if d.isoweekday() <= 5:
                uteis.append(d)
            d += timedelta(days=1)
        return uteis

    def test_grade_iso_folga_em_intervalo_feriado_e_horario_diferente(self):
        u = self._uteis(8)
        self.assertTrue({d.isoformat() for d in u} <= self._dias())
        # 2o sabado do mes: fora da folga em intervalo abaixo (folga vence horario diferente)
        sabado = next(self.inicio + timedelta(days=i) for i in range(7, 14)
                      if (self.inicio + timedelta(days=i)).isoweekday() == 6)
        self.assertNotIn(sabado.isoformat(), self._dias())
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=self.prof, data_inicio=u[0], data_fim=u[2])
        AgendaExcecao.objects.create(tipo='FOLGA', origem='FERIADO_NACIONAL', data_inicio=u[4], data_fim=u[4])
        AgendaExcecao.objects.create(tipo='FOLGA', origem='FERIADO_NACIONAL', ativo=False,
                                     data_inicio=u[5], data_fim=u[5])
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=None, data_inicio=u[6], data_fim=u[6])  # fechada
        AgendaExcecao.objects.create(tipo='HORARIO_DIFERENTE', profissional=self.prof, data_inicio=sabado,
                                     data_fim=sabado, hora_inicio=time(9), hora_fim=time(11))
        dias = self._dias()
        for d in (u[0], u[1], u[2], u[4], u[6]):
            self.assertNotIn(d.isoformat(), dias)
        for d in (u[3], u[5], sabado, u[7]):
            self.assertIn(d.isoformat(), dias)


class _Admin(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(email='agenda@test.com', password='senha-forte-123',
                                                      nome='Admin Agenda')
        self.client.force_login(self.admin)
        self.prof = criar_profissional('Dra. Agenda')


class PainelExcecoesTests(_Admin):
    def _criar(self, **dados):
        return self.client.post(reverse('aranha:admin_excecao_criar', args=[self.prof.pk]), dados)

    def test_folga_em_intervalo_uma_linha_e_reenvio_atualiza(self):
        d = timezone.localdate() + timedelta(days=10)
        self._criar(data=d.isoformat(), data_fim=(d + timedelta(days=6)).isoformat(), tipo='FOLGA', motivo='Ferias')
        folga = AgendaExcecao.objects.get(profissional=self.prof)
        self.assertEqual((folga.tipo, folga.data_inicio, folga.data_fim, folga.motivo, folga.usuario_autor_id),
                         ('FOLGA', d, d + timedelta(days=6), 'Ferias', self.admin.pk))
        self._criar(data=d.isoformat(), data_fim=(d + timedelta(days=6)).isoformat(), tipo='FOLGA', motivo='Viagem')
        folga.refresh_from_db()
        self.assertEqual(folga.motivo, 'Viagem')
        self.assertEqual(AgendaExcecao.objects.filter(profissional=self.prof).count(), 1)
        resp = self._criar(data=(d + timedelta(days=3)).isoformat(), tipo='FOLGA')
        self.assertTrue(any('Já existe folga' in m for m in _mensagens(resp)))
        self.assertEqual(AgendaExcecao.objects.filter(profissional=self.prof).count(), 1)
        pagina = self.client.get(reverse('aranha:admin_excecoes', args=[self.prof.pk]))
        self.assertContains(pagina, 'Viagem')
        self.assertContains(pagina, 'name="data_fim"')

    def test_horario_diferente_um_dia_e_data_fim_invertida(self):
        d = timezone.localdate() + timedelta(days=4)
        self._criar(data=d.isoformat(), tipo='HORARIO_DIFERENTE', hora_inicio='13:00', hora_fim='17:00',
                    data_fim=(d + timedelta(days=5)).isoformat())
        hd = AgendaExcecao.objects.get(profissional=self.prof)
        self.assertEqual((hd.data_inicio, hd.data_fim, hd.hora_inicio), (d, d, time(13)))
        resp = self._criar(data=d.isoformat(), data_fim=(d - timedelta(days=1)).isoformat(), tipo='FOLGA')
        self.assertTrue(any('data final' in m for m in _mensagens(resp)))

    def test_excluir_so_excecao_de_profissional(self):
        feriado = AgendaExcecao.objects.create(tipo='FOLGA', origem='FERIADO_LOCAL',
                                               data_inicio=date(2030, 1, 20), data_fim=date(2030, 1, 20))
        resp = self.client.post(reverse('aranha:admin_excecao_excluir', args=[feriado.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(AgendaExcecao.objects.filter(pk=feriado.pk).exists())


class CalendarioAgendaTests(_Admin):
    def test_eventos_expandem_recorrencia_e_mostram_feriado(self):
        limpar_feriados()  # os semeados pela 0012 podem cair na semana do teste
        segunda = timezone.localdate() + timedelta(days=(7 - timezone.localdate().weekday()) % 7 + 7)
        AgendaExcecao.objects.create(
            tipo='BLOQUEIO', profissional=self.prof, regra_recorrencia='FREQ=WEEKLY;BYDAY=MO,WE',
            data_hora_inicio=_local(segunda - timedelta(days=14), 12), data_hora_fim=_local(segunda - timedelta(days=14), 13),
        )
        AgendaExcecao.objects.create(tipo='FOLGA', origem='FERIADO_NACIONAL', motivo='Feriado X',
                                     data_inicio=segunda + timedelta(days=1), data_fim=segunda + timedelta(days=1))
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=self.prof, motivo='Ferias',
                                     data_inicio=segunda + timedelta(days=3), data_fim=segunda + timedelta(days=5))
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=None, motivo='Reforma',
                                     data_inicio=segunda + timedelta(days=2), data_fim=segunda + timedelta(days=2))
        resp = self.client.get(reverse('aranha:admin_calendar_events'), {
            'start': segunda.isoformat(), 'end': (segunda + timedelta(days=7)).isoformat(),
            'profissional': self.prof.pk,
        })
        eventos = resp.json()
        bloqueios = [e for e in eventos if str(e['id']).startswith('bloq-')]
        self.assertEqual(len(bloqueios), 2)  # segunda e quarta
        self.assertEqual(len({e['id'] for e in bloqueios}), 2)
        titulos = {e['title'] for e in eventos if str(e['id']).startswith('folga-')}
        self.assertEqual(titulos, {'Feriado nacional: Feriado X', 'Folga: Ferias', 'Clínica fechada: Reforma'})
        ferias = next(e for e in eventos if e['title'] == 'Folga: Ferias')
        self.assertEqual((ferias['start'], ferias['end']),
                         ((segunda + timedelta(days=3)).isoformat(), (segunda + timedelta(days=6)).isoformat()))

    def test_mover_para_ocorrencia_de_bloqueio_recorrente_da_409(self):
        proc = criar_procedimento(profissional=self.prof)
        at = criar_atendimento(criar_cliente(), self.prof, proc)
        alvo = timezone.localdate() + timedelta(days=15)
        AgendaExcecao.objects.create(
            tipo='BLOQUEIO', profissional=None, regra_recorrencia='FREQ=DAILY',
            data_hora_inicio=_local(alvo - timedelta(days=20), 12), data_hora_fim=_local(alvo - timedelta(days=20), 13),
        )
        resp = self.client.post(reverse('aranha:admin_calendar_mover'), data=json.dumps({
            'id': at.pk, 'start': _local(alvo, 12, 30).isoformat(), 'end': _local(alvo, 13, 30).isoformat(),
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 409)
        self.assertIn('bloqueio', resp.json()['mensagem'])

    def test_bloqueios_mostra_recorrencia(self):
        AgendaExcecao.objects.create(
            tipo='BLOQUEIO', profissional=self.prof, regra_recorrencia='FREQ=WEEKLY;BYDAY=FR',
            data_fim_recorrencia=date(2031, 1, 31),
            data_hora_inicio=_local(date(2030, 1, 4), 12), data_hora_fim=_local(date(2030, 1, 4), 13),
        )
        resp = self.client.get(reverse('aranha:admin_bloqueios'))
        self.assertContains(resp, 'Repete: FREQ=WEEKLY;BYDAY=FR até 31/01/2031')


class CarregarFeriadosTests(TestCase):
    def test_cria_folga_global_idempotente_e_respeita_inativo(self):
        AgendaExcecao.objects.filter(data_inicio__year=2035).delete()
        call_command('carregar_feriados', '--ano', '2035', stdout=StringIO())
        natal = AgendaExcecao.objects.get(origem='FERIADO_NACIONAL', data_inicio=date(2035, 12, 25))
        self.assertEqual((natal.tipo, natal.profissional_id, natal.data_fim, natal.ativo, natal.motivo),
                         ('FOLGA', None, date(2035, 12, 25), True, 'Natal'))
        total = AgendaExcecao.objects.filter(data_inicio__year=2035).count()
        self.assertEqual(total, 13)  # 9 fixos + 4 moveis
        natal.ativo = False  # a clinica abre no Natal de 2035
        natal.save()
        saida = StringIO()
        call_command('carregar_feriados', '--ano', '2035', stdout=saida)
        self.assertIn('0 criados, 13 ja existiam', saida.getvalue())
        natal.refresh_from_db()
        self.assertFalse(natal.ativo)
        self.assertEqual(AgendaExcecao.objects.filter(data_inicio__year=2035).count(), total)


class DjangoAdminAgendaTests(TestCase):
    """Admin de AgendaExcecao/AgendaHorario abre e o form normaliza a RRULE."""

    def setUp(self):
        from django.test import Client

        from .test_auth_equipe import _admin, _com_totp, _token

        self.admin = _admin(email='adm-agenda@test.com')
        device = _com_totp(self.admin)
        self.c = Client()
        self.c.force_login(self.admin)
        self.c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(device)})
        self.prof = criar_profissional('Dra. Admin')

    def test_telas_abrem(self):
        AgendaExcecao.objects.create(tipo='FOLGA', origem='FERIADO_LOCAL', motivo='Padroeira',
                                     data_inicio=date(2030, 8, 15), data_fim=date(2030, 8, 15))
        for url in ('/django-admin-sv/aranha_estetica/agendaexcecao/',
                    '/django-admin-sv/aranha_estetica/agendaexcecao/add/',
                    '/django-admin-sv/aranha_estetica/agendahorario/',
                    f'/django-admin-sv/aranha_estetica/profissional/{self.prof.pk}/change/'):
            with self.subTest(url=url):
                self.assertEqual(self.c.get(url).status_code, 200)

    def test_add_bloqueio_recorrente_normaliza_e_grava_autor(self):
        resp = self.c.post('/django-admin-sv/aranha_estetica/agendaexcecao/add/', {
            'tipo': 'BLOQUEIO', 'profissional': self.prof.pk, 'origem': 'MANUAL', 'ativo': 'on',
            'motivo': 'Curso', 'observacao': '',
            'data_hora_inicio_0': '2030-09-02', 'data_hora_inicio_1': '14:00',
            'data_hora_fim_0': '2030-09-02', 'data_hora_fim_1': '16:00',
            'regra_recorrencia': 'rrule:freq=weekly;byday=mo;count=4',
        })
        self.assertEqual(resp.status_code, 302, getattr(resp, 'context', None) and resp.context.get('errors'))
        bl = AgendaExcecao.objects.get(tipo='BLOQUEIO', profissional=self.prof)
        self.assertEqual((bl.regra_recorrencia, bl.data_fim_recorrencia, bl.usuario_autor_id),
                         ('FREQ=WEEKLY;BYDAY=MO', date(2030, 9, 23), self.admin.pk))

    def test_add_rrule_fora_do_subconjunto_mostra_erro(self):
        resp = self.c.post('/django-admin-sv/aranha_estetica/agendaexcecao/add/', {
            'tipo': 'BLOQUEIO', 'profissional': self.prof.pk, 'origem': 'MANUAL', 'ativo': 'on',
            'data_hora_inicio_0': '2030-09-02', 'data_hora_inicio_1': '14:00',
            'data_hora_fim_0': '2030-09-02', 'data_hora_fim_1': '16:00',
            'regra_recorrencia': 'FREQ=YEARLY',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Recorrência não suportada')
        self.assertFalse(AgendaExcecao.objects.filter(tipo='BLOQUEIO').exists())
