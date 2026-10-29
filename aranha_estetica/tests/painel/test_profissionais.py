"""Profissionais pelo ProfissionalForm + GradeSemanalForm: grade validada no clean, recusa por atendimento
orfao (057912d) mantida e feed ICS revogado ao desativar (decisao 30)."""
from datetime import datetime, time, timedelta

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.forms.cadastros import DIAS_SEMANA, GradeSemanalForm
from aranha_estetica.models import AgendaHorario, LinkAcesso, LogAuditoria, Usuario

from ..factories import criar_atendimento, criar_cliente, criar_link, criar_procedimento, criar_profissional

DIAS = [key for key, _nome in DIAS_SEMANA]


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


def _grade(**extra):
    dados = {}
    for key in DIAS:
        dados.update({f'trabalha_{key}': 'on', f'hora_inicio_{key}': '09:00', f'hora_fim_{key}': '18:00'})
    dados.update(extra)
    return dados


class GradeSemanalFormTests(TestCase):
    def test_fim_antes_do_inicio_e_dia_sem_hora_recusados(self):
        form = GradeSemanalForm(_grade(hora_inicio_segunda='14:00', hora_fim_segunda='09:00',
                                       hora_inicio_terca='', hora_fim_quarta='abc'))
        self.assertFalse(form.is_valid())
        self.assertEqual(form.non_field_errors(), [
            'Segunda-feira: a hora de fim deve ser depois da hora de início.',
            'Terça-feira: informe a hora de início e a de fim.',
            'Quarta-feira: informe a hora de início e a de fim.',
        ])
        self.assertNotIn('segunda', form.janelas)

    def test_fim_igual_ao_inicio_recusado(self):
        form = GradeSemanalForm(_grade(hora_inicio_sexta='10:00', hora_fim_sexta='10:00'))
        self.assertEqual(form.non_field_errors(), ['Sexta-feira: a hora de fim deve ser depois da hora de início.'])

    def test_dia_desmarcado_ignora_as_horas_e_aceita_segundos(self):
        form = GradeSemanalForm({'trabalha_segunda': 'on', 'hora_inicio_segunda': '08:30:00',
                                 'hora_fim_segunda': '12:00', 'hora_inicio_terca': 'lixo', 'hora_fim_terca': ''})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.janelas, {'segunda': (time(8, 30), time(12, 0))})

    def test_linhas_voltam_com_o_enviado(self):
        form = GradeSemanalForm({'trabalha_quinta': '1', 'hora_inicio_quinta': '07:00', 'hora_fim_quinta': '06:00'})
        linhas = {linha['key']: linha for linha in form.linhas()}
        self.assertTrue(linhas['quinta']['marcado'])
        self.assertEqual((linhas['quinta']['hora_inicio'], linhas['quinta']['hora_fim']), ('07:00', '06:00'))
        self.assertFalse(linhas['segunda']['marcado'])
        self.assertEqual(linhas['segunda']['hora_inicio'], '09:00')


class _Admin(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(email='dona-prof@test.com', password='senha-forte-123',
                                                      nome='Dona')
        self.client.force_login(self.admin)
        self.prof = criar_profissional('Dra. Bia')   # 7 dias 09:00-18:00
        self.prof.refresh_from_db()
        self.proc = criar_procedimento(profissional=self.prof)
        self.url = reverse('aranha:profissional_editar', args=[self.prof.pk])

    def _post(self, **extra):
        return self.client.post(self.url, {'nome': self.prof.nome, 'ativo': '1',
                                           'procedimentos': [str(self.proc.pk)], **_grade(**extra)})


class EditarProfissionalTests(_Admin):
    def test_grade_com_fim_antes_do_inicio_recusada_sem_gravar(self):
        resp = self._post(hora_inicio_sabado='15:00', hora_fim_sabado='08:00', nome='Dra. Bia Nova')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Sábado: a hora de fim deve ser depois da hora de início.', _mensagens(resp))
        self.assertContains(resp, 'value="Dra. Bia Nova"')
        self.prof.refresh_from_db()
        self.assertEqual(self.prof.nome, 'Dra. Bia')
        self.assertEqual(AgendaHorario.objects.filter(profissional=self.prof, dia_semana=6).get().hora_inicio,
                         time(9, 0))

    def test_nome_em_branco_recusado_com_o_erro_no_campo(self):
        resp = self._post(nome='   ')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('O nome do profissional é obrigatório.', _mensagens(resp))
        self.assertContains(resp, 'id="nome-erro"')
        self.assertContains(resp, 'Dra. Bia')   # subtitulo da pagina = o nome gravado

    def test_recusa_por_atendimento_orfao_continua_e_leva_a_agenda_filtrada(self):
        dia = timezone.localdate() + timedelta(days=3)
        cliente = criar_cliente('Carla Souza')
        criar_atendimento(cliente, self.prof, self.proc,
                          data_hora=timezone.make_aware(datetime.combine(dia, time(15, 0))))
        resp = self._post(ativo='')
        self.assertEqual(resp.status_code, 200)
        esperado = (f'1 atendimento(s) futuro(s) ficariam sem quem atenda: {dia:%d/%m} 15:00 {self.proc.nome} '
                    f'(Carla Souza). Reagende ou cancele antes (Painel > Agendamentos).')
        self.assertIn(esperado, _mensagens(resp))
        self.assertContains(resp, f'{reverse("aranha:painel_agendamentos")}?profissional={self.prof.pk}')
        self.prof.refresh_from_db()
        self.assertTrue(self.prof.ativo)

    def test_sem_orfao_nao_mostra_o_atalho(self):
        resp = self._post(hora_inicio_domingo='18:00', hora_fim_domingo='09:00')
        self.assertNotContains(resp, '?profissional=')


class FeedIcsAoDesativarTests(_Admin):
    def setUp(self):
        super().setUp()
        self.token = criar_link(LinkAcesso.FEED_ICS, self.prof)

    def _feed(self, token):
        return self.client.get(f'/agenda/{self.prof.slug}/feed.ics', {'token': token}).status_code

    def test_desativar_revoga_o_feed_e_reativar_nao_o_devolve(self):
        self.assertEqual(self._feed(self.token), 200)
        resp = self._post(ativo='')
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(LinkAcesso.objects.get(profissional=self.prof).revogado_em)
        log = LogAuditoria.objects.filter(tabela='profissional', acao__contains='feed ICS').get()
        self.assertEqual(log.detalhes, {'motivo': 'profissional desativado'})
        self.assertTrue(any('gere um link novo' in m for m in _mensagens(resp)))
        self.assertEqual(self._post().status_code, 302)   # reativa
        self.prof.refresh_from_db()
        self.assertTrue(self.prof.ativo)
        self.assertEqual(self._feed(self.token), 404)

    def test_editar_sem_desativar_mantem_o_feed(self):
        self.assertEqual(self._post(nome='Dra. Bia Souza').status_code, 302)
        self.assertEqual(self._feed(self.token), 200)


class CadastrarProfissionalTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(email='dona-cad@test.com', password='senha-forte-123',
                                                      nome='Dona')
        self.client.force_login(self.admin)

    def test_grade_invalida_volta_com_o_digitado(self):
        resp = self.client.post(reverse('aranha:profissional_cadastro'), {
            'nome': 'Dra. Nova', 'especialidade': 'Drenagem', 'ativo': '1',
            'trabalha_segunda': 'on', 'hora_inicio_segunda': '18:00', 'hora_fim_segunda': '09:00'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Segunda-feira: a hora de fim deve ser depois da hora de início.', _mensagens(resp))
        self.assertContains(resp, 'value="Dra. Nova"')
        self.assertContains(resp, 'value="Drenagem"')
        self.assertTrue(resp.context['dias'][0]['marcado'])
        self.assertTrue(resp.context['form_ativo'])

    def test_get_vem_ativo_por_padrao(self):
        resp = self.client.get(reverse('aranha:profissional_cadastro'))
        self.assertTrue(resp.context['form_ativo'])
        self.assertFalse(any(d['marcado'] for d in resp.context['dias']))

    def test_get_nas_acoes_so_post(self):
        prof = criar_profissional()
        self.assertEqual(self.client.get(reverse('aranha:profissional_rotacionar_ics', args=[prof.pk])).status_code,
                         405)
