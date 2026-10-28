"""Lista de espera (decisao 17): inscricao publica sem criar Cliente, anti-abuso e aviso util.

Inscricao anonima guarda so nome/telefone de contato; liga ao cadastro (e aceita e-mail) so com o
celular verificado por OTP. O aviso respeita turno e antecedencia, nao tira da fila, e agendar
encerra a inscricao.
"""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import Cliente, ListaEspera
from aranha_estetica.services import lista_espera
from aranha_estetica.services import otp as otp_service
from aranha_estetica.utils import datas

from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional

ENVIAR_EMAIL = 'aranha_estetica.utils.email.enviar_fila_espera_email'
ENVIAR_WA = 'aranha_estetica.utils.whatsapp.enviar_template'


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.url = reverse('aranha:lista_espera_publica')

    def _post(self, **overrides):
        data = {
            'nome': 'Ana Cliente',
            'telefone': '17988880000',
            'email': 'ana@exemplo.com',
            'procedimento': self.proc.pk,
            'data_desejada': (datas.hoje() + timedelta(days=5)).isoformat(),
            'turno': 'TARDE',
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def _verificar_telefone_na_sessao(self, digitos='17988880000'):
        """Simula o celular confirmado por SMS no wizard (otp.registrar_verificacao_agendamento)."""
        session = self.client.session
        session[otp_service.SESSAO_AGENDAMENTO] = digitos
        session[otp_service.SESSAO_AGENDAMENTO_EXPIRA] = (timezone.now() + timedelta(minutes=30)).isoformat()
        session.save()


@override_settings(RATELIMIT_ENABLE=False)
class InscricaoPublicaTests(_Base):
    def test_get_renderiza_formulario(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Lista de Espera')
        self.assertContains(resp, self.proc.nome)
        self.assertContains(resp, 'name="email"')
        self.assertContains(resp, 'name="website"')   # honeypot
        self.assertContains(resp, 'Manhã')

    def test_inscricao_anonima_nao_cria_cliente_nem_guarda_email(self):
        resp = self._post()
        self.assertRedirects(resp, reverse('aranha:lista_espera_sucesso'))
        self.assertFalse(Cliente.objects.exists())
        espera = ListaEspera.objects.get()
        self.assertIsNone(espera.cliente)
        self.assertEqual((espera.nome_contato, espera.telefone_contato), ('Ana Cliente', '17988880000'))
        self.assertIsNone(espera.email_contato)   # e-mail sem celular verificado e ignorado
        self.assertEqual(espera.turno_desejado, 'TARDE')

    def test_cliente_existente_sem_verificacao_nao_e_tocada_nem_ligada(self):
        """Regressao (security-12 / rev_security-01): saber o telefone nao renomeia a cliente nem prende
        e-mail de terceiro ao cadastro dela."""
        dona = Cliente.objects.create(nome='Ana Verdadeira', telefone='17988880000')
        resp = self._post(nome='Hacker', email='hacker@exemplo.com')
        self.assertRedirects(resp, reverse('aranha:lista_espera_sucesso'))   # nao revela cadastro
        dona.refresh_from_db()
        self.assertEqual((dona.nome, dona.email), ('Ana Verdadeira', None))
        espera = ListaEspera.objects.get()
        self.assertIsNone(espera.cliente)
        self.assertIsNone(espera.email_contato)

    def test_celular_verificado_liga_ao_cadastro_e_aceita_email(self):
        dona = Cliente.objects.create(nome='Ana Verdadeira', telefone='17988880000')
        self._verificar_telefone_na_sessao('17988880000')
        self._post(email='ana.nova@exemplo.com')
        espera = ListaEspera.objects.get()
        self.assertEqual((espera.cliente, espera.email_contato), (dona, 'ana.nova@exemplo.com'))
        self.assertEqual(espera.nome_contato, '')
        dona.refresh_from_db()
        self.assertIsNone(dona.email)   # cadastro continua intocado

    def test_verificacao_de_outro_telefone_nao_libera_email(self):
        Cliente.objects.create(nome='Ana Verdadeira', telefone='17988880000')
        self._verificar_telefone_na_sessao('17911112222')
        self._post(email='atacante@evil.test')
        espera = ListaEspera.objects.get()
        self.assertIsNone(espera.cliente)
        self.assertIsNone(espera.email_contato)

    def test_telefone_com_ddi_55_e_normalizado(self):
        """Regressao (pgmig-08): '+55 ...' virava 13 digitos e estourava o CHECK do Postgres (500)."""
        resp = self._post(telefone='+55 (17) 98888-0000')
        self.assertRedirects(resp, reverse('aranha:lista_espera_sucesso'))
        self.assertEqual(ListaEspera.objects.get().telefone_contato, '17988880000')

    def test_telefone_sem_ddd_volta_com_mensagem_sem_gravar(self):
        for tel in ('98888-0000', 'abc', '123'):
            resp = self._post(telefone=tel)
            self.assertEqual(resp.status_code, 200, tel)
            self.assertTrue(any('celular válido' in str(m) for m in resp.context['messages']), tel)
        self.assertEqual(ListaEspera.objects.count(), 0)

    def test_erros_de_validacao(self):
        casos = {
            'email': {'email': 'sem-arroba'},
            'data passada': {'data_desejada': (datas.hoje() - timedelta(days=1)).isoformat()},
            'sem nome': {'nome': ''},
            'turno inexistente': {'turno': 'MADRUGADA'},
            'procedimento inexistente': {'procedimento': 999999},
        }
        for caso, dados in casos.items():
            with self.subTest(caso):
                resp = self._post(**dados)
                self.assertEqual(resp.status_code, 200)
        self.assertEqual(ListaEspera.objects.count(), 0)

    def test_erro_preserva_o_que_foi_digitado(self):
        resp = self._post(telefone='123', nome='Maria Clara')
        self.assertContains(resp, 'value="Maria Clara"')

    def test_aceita_hoje_no_fuso_local(self):
        resp = self._post(data_desejada=datas.hoje().isoformat())
        self.assertRedirects(resp, reverse('aranha:lista_espera_sucesso'))

    def test_evita_inscricao_duplicada(self):
        self._post()
        self._post()
        self.assertEqual(ListaEspera.objects.count(), 1)

    def test_profissional_opcional(self):
        self._post(profissional='')
        self.assertIsNone(ListaEspera.objects.get().profissional_desejado)

    def test_honeypot_descarta_em_silencio(self):
        resp = self._post(website='http://spam.example')
        self.assertRedirects(resp, reverse('aranha:lista_espera_sucesso'))
        self.assertEqual(ListaEspera.objects.count(), 0)

    @override_settings(TURNSTILE_SECRET_KEY='segredo-teste')
    @patch('aranha_estetica.utils.captcha.verificar_turnstile', return_value=False)
    def test_sem_captcha_valido_da_400(self, _verificar):
        resp = self._post()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(ListaEspera.objects.count(), 0)


class LimiteDiarioTests(_Base):
    def test_21o_post_do_dia_da_429(self):
        for i in range(20):
            resp = self._post(telefone=f'179880{i:05d}')
            self.assertEqual(resp.status_code, 302, i)
        self.assertEqual(self._post(telefone='17988899999').status_code, 429)


class AvisoDeVagaTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.ocupante = criar_cliente(nome='Ocupante')
        self.espera_cli = criar_cliente(nome='Na Fila', email='fila@example.com', consent_whatsapp_confirmacao=True)

    def _vaga(self, quando):
        return criar_atendimento(self.ocupante, self.prof, self.proc, status='AGENDADO', data_hora=quando)

    def _local(self, dias, hora):
        local = timezone.localtime(timezone.now() + timedelta(days=dias)).replace(
            hour=hora, minute=0, second=0, microsecond=0)
        return local

    def _inscrever(self, quando, turno=None, cliente=None):
        return ListaEspera.objects.create(
            cliente=cliente or self.espera_cli, procedimento=self.proc,
            data_desejada=timezone.localdate(quando), turno_desejado=turno,
        )

    def test_turno_manha_nao_recebe_vaga_das_17h(self):
        vaga = self._vaga(self._local(3, 17))
        self._inscrever(vaga.data_hora_inicio, turno='MANHA')
        self.assertEqual(lista_espera.candidatos(vaga), [])
        tarde = self._inscrever(vaga.data_hora_inicio, turno='TARDE',
                                cliente=criar_cliente(nome='Prefere Tarde'))
        self.assertEqual(lista_espera.candidatos(vaga), [tarde])

    def test_turno_de(self):
        for hora, turno in ((8, 'MANHA'), (11, 'MANHA'), (12, 'TARDE'), (17, 'TARDE'), (18, 'NOITE')):
            self.assertEqual(lista_espera.turno_de(self._local(1, hora)), turno, hora)

    def test_vaga_dentro_da_antecedencia_minima_nao_avisa(self):
        self.prof.min_notice_horas = 2
        self.prof.save(update_fields=['min_notice_horas'])
        vaga = self._vaga(timezone.now() + timedelta(minutes=90))
        self._inscrever(vaga.data_hora_inicio)
        self.assertEqual(lista_espera.candidatos(vaga), [])

    @patch(ENVIAR_WA, return_value=False)
    @patch(ENVIAR_EMAIL, return_value=True)
    def test_avisada_continua_na_fila_e_link_traz_a_vaga(self, mock_email, _wa):
        vaga = self._vaga(self._local(3, 15))
        espera = self._inscrever(vaga.data_hora_inicio)
        with self.captureOnCommitCallbacks(execute=True):
            vaga.cancelar(motivo='teste')
        espera.refresh_from_db()
        self.assertIsNotNone(espera.ultimo_aviso_em)
        link = mock_email.call_args[0][1]['link']
        self.assertIn(f'procedimento={self.proc.pk}', link)
        self.assertIn(f'profissional={self.prof.pk}', link)
        self.assertIn(f'data={timezone.localdate(vaga.data_hora_inicio).isoformat()}', link)

    @patch(ENVIAR_WA, return_value=True)
    @patch(ENVIAR_EMAIL, return_value=True)
    def test_inscricao_anonima_nao_recebe_aviso_automatico(self, mock_email, mock_wa):
        vaga = self._vaga(self._local(3, 15))
        anonima = ListaEspera.objects.create(
            nome_contato='Sem Cadastro', telefone_contato='17988887777', procedimento=self.proc,
            data_desejada=timezone.localdate(vaga.data_hora_inicio),
        )
        with self.captureOnCommitCallbacks(execute=True):
            vaga.cancelar(motivo='teste')
        mock_email.assert_not_called()
        mock_wa.assert_not_called()
        self.assertTrue(ListaEspera.objects.filter(pk=anonima.pk).exists())   # a recepcao faz o contato

    def test_agendar_encerra_a_inscricao(self):
        quando = self._local(4, 10)
        self._inscrever(quando)
        anonima = ListaEspera.objects.create(
            nome_contato='Na Fila', telefone_contato=self.espera_cli.telefone, procedimento=self.proc,
            data_desejada=timezone.localdate(quando) + timedelta(days=1),
        )
        outro_proc = criar_procedimento(nome='Outro', profissional=self.prof)
        fica = ListaEspera.objects.create(cliente=self.espera_cli, procedimento=outro_proc,
                                          data_desejada=timezone.localdate(quando))
        from aranha_estetica.services import agenda
        agenda.reservar(cliente=self.espera_cli, profissional=self.prof, procedimento=self.proc,
                        inicio=quando, status='AGENDADO')
        self.assertEqual(list(ListaEspera.objects.all()), [fica])
        self.assertFalse(ListaEspera.objects.filter(pk=anonima.pk).exists())
