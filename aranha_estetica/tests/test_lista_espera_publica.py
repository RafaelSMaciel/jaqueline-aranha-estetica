"""Testes da inscricao publica na lista de espera."""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import Cliente, ListaEspera
from aranha_estetica.services import otp as otp_service
from aranha_estetica.utils import datas

from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional


@override_settings(RATELIMIT_ENABLE=False)
class ListaEsperaPublicaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.prof = criar_profissional()
        self.proc = criar_procedimento()
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

    def test_get_renderiza_formulario(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Lista de Espera')
        self.assertContains(resp, self.proc.nome)
        self.assertContains(resp, 'name="email"')
        self.assertContains(resp, 'Manhã')

    def _verificar_telefone_na_sessao(self, digitos='17988880000'):
        """Simula o celular confirmado por SMS no wizard (otp.registrar_verificacao_agendamento)."""
        session = self.client.session
        session[otp_service.SESSAO_AGENDAMENTO] = digitos
        session[otp_service.SESSAO_AGENDAMENTO_EXPIRA] = (timezone.now() + timedelta(minutes=30)).isoformat()
        session.save()

    def test_post_valido_cria_cliente_e_inscricao(self):
        resp = self._post()
        self.assertRedirects(resp, reverse('aranha:lista_espera_sucesso'))
        cliente = Cliente.objects.get(telefone='17988880000')
        # Form anonimo nao prova posse do e-mail: nao entra no cadastro (rev_security-01)
        self.assertIn(cliente.email, (None, ''))
        espera = ListaEspera.objects.get()
        self.assertEqual(espera.email_contato, 'ana@exemplo.com')  # aviso desta vaga

    def test_reaproveita_cliente_existente(self):
        Cliente.objects.create(nome='Ana Cliente', telefone='17988880000')
        self._post()
        self.assertEqual(Cliente.objects.filter(telefone='17988880000').count(), 1)

    def test_form_anonimo_nao_sobrescreve_dados_de_cliente_existente(self):
        """Regressao (security-12): qualquer um renomeava a cliente so sabendo o telefone."""
        Cliente.objects.create(nome='Ana Verdadeira', telefone='17988880000')
        resp = self._post(nome='Hacker', email='hacker@exemplo.com')
        self.assertRedirects(resp, reverse('aranha:lista_espera_sucesso'))
        cliente = Cliente.objects.get(telefone='17988880000')
        self.assertEqual(cliente.nome, 'Ana Verdadeira')
        self.assertIn(cliente.email, (None, ''))
        self.assertEqual(ListaEspera.objects.filter(cliente=cliente).count(), 1)

    def test_email_de_outro_cliente_nao_quebra_unique(self):
        Cliente.objects.create(nome='Outra', telefone='17911112222', email='ana@exemplo.com')
        resp = self._post()
        self.assertRedirects(resp, reverse('aranha:lista_espera_sucesso'))
        novo = Cliente.objects.get(telefone='17988880000')
        self.assertIn(novo.email, (None, ''))

    def test_email_de_anonimo_nao_fica_na_inscricao_de_cliente_existente(self):
        """Regressao rev_security-01: sem OTP, qualquer um prendia o proprio e-mail
        ao telefone de terceiros e recebia o aviso de vaga com o nome da cliente."""
        Cliente.objects.create(nome='Ana Verdadeira', telefone='17988880000')
        resp = self._post(email='atacante@evil.test')
        self.assertRedirects(resp, reverse('aranha:lista_espera_sucesso'))  # nao revela cadastro
        espera = ListaEspera.objects.get(cliente__telefone='17988880000')
        self.assertIsNone(espera.email_contato)
        self.assertIn(espera.cliente.email, (None, ''))  # cadastro continua intocado

    def test_email_digitado_fica_na_inscricao_com_celular_verificado(self):
        """Regressao public_front-08: cliente antiga sem e-mail recebe o aviso
        quando provou a posse do celular (SMS do wizard nesta sessao)."""
        Cliente.objects.create(nome='Ana Verdadeira', telefone='17988880000')
        self._verificar_telefone_na_sessao('17988880000')
        self._post(email='ana.nova@exemplo.com')
        espera = ListaEspera.objects.get(cliente__telefone='17988880000')
        self.assertEqual(espera.email_contato, 'ana.nova@exemplo.com')
        self.assertIn(espera.cliente.email, (None, ''))  # cadastro continua intocado

    def test_verificacao_de_outro_telefone_nao_libera_email(self):
        Cliente.objects.create(nome='Ana Verdadeira', telefone='17988880000')
        self._verificar_telefone_na_sessao('17911112222')
        self._post(email='atacante@evil.test')
        self.assertIsNone(ListaEspera.objects.get().email_contato)

    @patch('aranha_estetica.utils.whatsapp.enviar_template_whatsapp', return_value=False)
    @patch('aranha_estetica.utils.email.enviar_fila_espera_email', return_value=True)
    def test_aviso_de_vaga_nao_vai_para_email_plantado(self, mock_email, _mock_wa):
        """Regressao rev_security-01 (ponta a ponta): vaga cancelada nao manda
        nome da cliente + horario p/ o e-mail digitado por terceiro."""
        prof = criar_profissional(nome='Dra. Vaga')
        dona = criar_cliente(nome='Beatriz Confidencial', telefone='17988880000')
        quando = timezone.now() + timedelta(days=3)
        outra = criar_cliente(nome='Outra Pessoa')
        atend = criar_atendimento(outra, prof, self.proc, status='AGENDADO', data_hora=quando)
        self._post(email='atacante@evil.test', data_desejada=datas.data_local(quando).isoformat())
        self.assertTrue(ListaEspera.objects.filter(cliente=dona).exists())

        with self.captureOnCommitCallbacks(execute=True):
            atend.cancelar(motivo='teste')

        destinos = [c.args[0] for c in mock_email.call_args_list]
        self.assertNotIn('atacante@evil.test', destinos)

    def test_cadastro_novo_nao_recebe_email_do_form_anonimo(self):
        """Regressao rev_security-01 (b): e-mail plantado num telefone ainda sem
        cadastro travava o e-mail do cadastro (a dona, com OTP, nao conseguia trocar)."""
        self._post(email='atacante@evil.test')
        cliente = Cliente.objects.get(telefone='17988880000')
        self.assertIn(cliente.email, (None, ''))

    def test_telefone_com_ddi_55_e_normalizado(self):
        """Regressao (pgmig-08): '+55 ...' virava 13 digitos e estourava o CHECK do Postgres (500)."""
        resp = self._post(telefone='+55 (17) 98888-0000')
        self.assertRedirects(resp, reverse('aranha:lista_espera_sucesso'))
        self.assertTrue(Cliente.objects.filter(telefone='17988880000').exists())

    def test_telefone_sem_ddd_volta_com_mensagem_sem_gravar(self):
        """Regressao (pgtests-03): sem DDD ia direto ao banco (CHECK 10-11 digitos -> 500)."""
        for tel in ('98888-0000', 'abc', '123'):
            resp = self._post(telefone=tel)
            self.assertEqual(resp.status_code, 200, tel)
            self.assertTrue(any('celular válido' in str(m) for m in resp.context['messages']), tel)
        self.assertEqual(Cliente.objects.count(), 0)
        self.assertEqual(ListaEspera.objects.count(), 0)

    def test_email_invalido_volta_com_mensagem(self):
        resp = self._post(email='sem-arroba')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ListaEspera.objects.count(), 0)

    def test_erro_preserva_o_que_foi_digitado(self):
        resp = self._post(telefone='123', nome='Maria Clara')
        self.assertContains(resp, 'value="Maria Clara"')

    def test_rejeita_data_passada(self):
        resp = self._post(data_desejada=(datas.hoje() - timedelta(days=1)).isoformat())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ListaEspera.objects.count(), 0)

    def test_aceita_hoje_no_fuso_local(self):
        """Regressao (public_front-14): apos 21h BRT a data UTC ja e amanha e 'hoje' era recusado."""
        resp = self._post(data_desejada=datas.hoje().isoformat())
        self.assertRedirects(resp, reverse('aranha:lista_espera_sucesso'))

    def test_rejeita_campos_obrigatorios_ausentes(self):
        resp = self._post(nome='')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ListaEspera.objects.count(), 0)

    def test_evita_inscricao_duplicada(self):
        self._post()
        self._post()  # mesma combinacao cliente+procedimento+data
        self.assertEqual(ListaEspera.objects.count(), 1)

    def test_turno_invalido_vira_nulo(self):
        self._post(turno='MADRUGADA')
        inscricao = ListaEspera.objects.first()
        self.assertIsNone(inscricao.turno_desejado)

    def test_profissional_opcional(self):
        self._post(profissional='')
        inscricao = ListaEspera.objects.first()
        self.assertIsNone(inscricao.profissional_desejado)

    def test_integrity_error_vira_mensagem_e_nao_500(self):
        from django.db import IntegrityError
        with patch('aranha_estetica.views.public.ListaEspera.objects.create', side_effect=IntegrityError('x')):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any('Não foi possível' in str(m) for m in resp.context['messages']))
