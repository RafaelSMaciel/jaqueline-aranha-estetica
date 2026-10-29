"""Excecoes e bloqueios de agenda (P06): ModelForm com as mensagens da tela, upsert pelo manager e
o AgendaExcecao.clean dividido por regra."""
from datetime import date, time, timedelta

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.forms.agenda import AgendaExcecaoForm
from aranha_estetica.models import AgendaExcecao, LogAuditoria, Usuario

from ..factories import criar_profissional


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class _Admin(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email='admin-exc-p06@test.com', password='senha-forte-123', nome='Admin Exceções',
        )
        self.client.force_login(self.admin)
        self.prof = criar_profissional('Dra. Exceção')
        self.dia = timezone.localdate() + timedelta(days=10)


class ExcecoesTests(_Admin):
    def _criar(self, **dados):
        resp = self.client.post(reverse('aranha:admin_excecao_criar', args=[self.prof.pk]), dados)
        self.client.get(reverse('aranha:admin_excecoes', args=[self.prof.pk]))   # a tela consome os avisos
        return resp

    def test_mensagens_de_hoje(self):
        d = self.dia.isoformat()
        casos = {
            'Data e tipo são obrigatórios.': [{'tipo': 'FOLGA'}, {'data': d, 'tipo': 'BLOQUEIO'}, {'data': d}],
            'Data inválida.': [{'data': '31/12/2030', 'tipo': 'FOLGA'},
                               {'data': d, 'data_fim': 'amanha', 'tipo': 'FOLGA'}],
            'Para horário diferente, informe a hora de início e a de fim.': [
                {'data': d, 'tipo': 'HORARIO_DIFERENTE', 'hora_inicio': '09:00'}],
            'Hora inválida.': [{'data': d, 'tipo': 'HORARIO_DIFERENTE', 'hora_inicio': '25:99', 'hora_fim': '26:00'}],
            'A hora de fim deve ser depois da hora de início.': [
                {'data': d, 'tipo': 'HORARIO_DIFERENTE', 'hora_inicio': '14:00', 'hora_fim': '09:00'}],
            'A data final da folga deve ser igual ou posterior à inicial.': [
                {'data': d, 'data_fim': (self.dia - timedelta(days=1)).isoformat(), 'tipo': 'FOLGA'}],
        }
        for mensagem, envios in casos.items():
            for dados in envios:
                with self.subTest(dados=dados):
                    resp = self._criar(**dados)
                    self.assertRedirects(resp, reverse('aranha:admin_excecoes', args=[self.prof.pk]),
                                         fetch_redirect_response=False)
                    self.assertEqual(_mensagens(resp), [mensagem])
        self.assertFalse(AgendaExcecao.objects.filter(profissional=self.prof).exists())

    def test_folga_sobreposta_recusada_pela_regra_do_model(self):
        self._criar(data=self.dia.isoformat(), data_fim=(self.dia + timedelta(days=5)).isoformat(), tipo='FOLGA')
        resp = self._criar(data=(self.dia + timedelta(days=2)).isoformat(),
                           data_fim=(self.dia + timedelta(days=8)).isoformat(), tipo='FOLGA')
        self.assertEqual(_mensagens(resp), ['Já existe folga deste profissional nesse período.'])
        self.assertEqual(AgendaExcecao.objects.filter(profissional=self.prof).count(), 1)

    def test_reenvio_do_mesmo_dia_atualiza_e_trilha(self):
        d = self.dia.isoformat()
        resp = self._criar(data=d, tipo='HORARIO_DIFERENTE', hora_inicio='13:00', hora_fim='17:00', motivo=' Meio ')
        self.assertEqual(_mensagens(resp), ['Exceção criada.'])
        resp = self._criar(data=d, tipo='HORARIO_DIFERENTE', hora_inicio='08:00', hora_fim='12:00')
        self.assertEqual(_mensagens(resp), ['Exceção atualizada.'])
        hd = AgendaExcecao.objects.get(profissional=self.prof)
        self.assertEqual((hd.hora_inicio, hd.hora_fim, hd.data_fim, hd.usuario_autor_id, hd.origem),
                         (time(8), time(12), self.dia, self.admin.pk, 'MANUAL'))
        self.assertEqual(LogAuditoria.objects.filter(tabela='agenda_excecao', registro_id=hd.pk).count(), 2)

    def test_folga_ignora_horas_e_sem_fim_vale_um_dia(self):
        form = AgendaExcecaoForm({'data': self.dia.isoformat(), 'tipo': 'FOLGA', 'hora_inicio': '10:00'},
                                 profissional=self.prof)
        self.assertTrue(form.is_valid(), form.errors)
        folga = form.save()
        self.assertEqual((folga.data_inicio, folga.data_fim, folga.hora_inicio), (self.dia, self.dia, None))

    def test_get_nao_cria(self):
        self.assertEqual(self.client.get(reverse('aranha:admin_excecao_criar', args=[self.prof.pk])).status_code, 405)


class AgendaExcecaoCleanTests(TestCase):
    """O clean dividido por regra mantem as mensagens e as normalizacoes."""

    def setUp(self):
        self.prof = criar_profissional('Dra. Clean')

    def _erros(self, **campos):
        with self.assertRaises(ValidationError) as ctx:
            AgendaExcecao(**campos).full_clean()
        return {campo: msgs[0] for campo, msgs in ctx.exception.message_dict.items()}

    def test_regras_por_tipo(self):
        agora = timezone.now()
        self.assertEqual(self._erros(tipo='FOLGA', profissional=self.prof)['data_inicio'], 'Informe a data.')
        self.assertEqual(self._erros(tipo='FOLGA', data_inicio=date(2030, 1, 2), data_fim=date(2030, 1, 1))['data_fim'],
                         'A data final deve ser igual ou posterior à inicial.')
        self.assertEqual(self._erros(tipo='HORARIO_DIFERENTE', data_inicio=date(2030, 1, 2))['profissional'],
                         'Horário diferente é sempre de um profissional.')
        self.assertEqual(self._erros(tipo='BLOQUEIO', data_hora_inicio=agora, data_hora_fim=agora)['data_hora_fim'],
                         'O fim deve ser posterior ao início.')
        self.assertIn('Recorrência não suportada',
                      self._erros(tipo='BLOQUEIO', data_hora_inicio=agora, data_hora_fim=agora + timedelta(hours=1),
                                  regra_recorrencia='FREQ=YEARLY')['regra_recorrencia'])
        self.assertEqual(self._erros(tipo='BLOQUEIO', profissional=self.prof, origem='FERIADO_NACIONAL',
                                     data_hora_inicio=agora, data_hora_fim=agora + timedelta(hours=1))['origem'],
                         'Feriado/recesso é FOLGA da clínica inteira (sem profissional).')

    def test_normaliza_vazios(self):
        hd = AgendaExcecao(tipo='HORARIO_DIFERENTE', profissional=self.prof, data_inicio=date(2030, 1, 2),
                           data_fim=date(2030, 1, 9), hora_inicio=time(9), hora_fim=time(12))
        hd.full_clean()
        self.assertEqual(hd.data_fim, date(2030, 1, 2))
        agora = timezone.now()
        bloqueio = AgendaExcecao(tipo='BLOQUEIO', data_hora_inicio=agora, data_hora_fim=agora + timedelta(hours=1),
                                 regra_recorrencia='  ')
        bloqueio.full_clean()
        self.assertIsNone(bloqueio.regra_recorrencia)

    def test_vigente_para(self):
        folga = AgendaExcecao.objects.create(tipo='FOLGA', profissional=self.prof, data_inicio=date(2030, 1, 2),
                                             data_fim=date(2030, 1, 5))
        vigente = AgendaExcecao.objects.vigente_para
        self.assertEqual(vigente(self.prof, 'FOLGA', date(2030, 1, 2), date(2030, 1, 5)), folga)
        self.assertIsNone(vigente(self.prof, 'FOLGA', date(2030, 1, 2), date(2030, 1, 4)))
        hd = AgendaExcecao.objects.create(tipo='HORARIO_DIFERENTE', profissional=self.prof, data_inicio=date(2030, 2, 1),
                                          data_fim=date(2030, 2, 1), hora_inicio=time(9), hora_fim=time(10))
        self.assertEqual(vigente(self.prof, 'HORARIO_DIFERENTE', date(2030, 2, 1)), hd)


class BloqueiosTests(_Admin):
    def _criar(self, **extra):
        self.client.get(reverse('aranha:admin_bloqueios'))   # consome os avisos do envio anterior
        inicio = timezone.localtime(timezone.now() + timedelta(days=2)).replace(hour=12, minute=0, second=0,
                                                                               microsecond=0)
        dados = {'profissional_id': str(self.prof.pk), 'data_hora_inicio': inicio.strftime('%Y-%m-%dT%H:%M'),
                 'data_hora_fim': (inicio + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'), 'motivo': 'Curso'}
        dados.update(extra)
        return self.client.post(reverse('aranha:admin_criar_bloqueio'), dados)

    def test_mensagem_por_campo(self):
        resp = self._criar(profissional_id='', data_hora_inicio='ontem')
        self.assertEqual(_mensagens(resp), ['Profissional: Escolha o profissional ou "Todos os profissionais".',
                                            'Início: Data e hora inválidas.'])
        resp = self._criar(profissional_id='9' * 30)
        self.assertEqual(_mensagens(resp), ['Profissional não encontrado: escolha um da lista.'])
        self.assertFalse(AgendaExcecao.objects.filter(tipo='BLOQUEIO').exists())

    def test_cria_e_get_nao_cria(self):
        resp = self._criar()
        self.assertEqual(_mensagens(resp), ['Bloqueio criado com sucesso!'])
        bloqueio = AgendaExcecao.objects.get(tipo='BLOQUEIO')
        self.assertEqual((bloqueio.profissional_id, bloqueio.motivo, bloqueio.usuario_autor_id),
                         (self.prof.pk, 'Curso', self.admin.pk))
        self.assertEqual(self.client.get(reverse('aranha:admin_criar_bloqueio')).status_code, 405)
