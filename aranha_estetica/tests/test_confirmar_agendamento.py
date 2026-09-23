"""Testes do fluxo publico de agendamento (POST /agendamento/confirmar/)."""
import json
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import (
    Atendimento,
    Cliente,
    ExcecaoDisponibilidade,
    Feriado,
    FormularioAnamnese,
    Habilitacao,
    RespostaAnamnese,
)

from .factories import (
    criar_procedimento,
    criar_profissional,
)


def slot_local(dias=3, hora=10, minuto=0):
    """ISO de um horario LOCAL futuro dentro do expediente da factory (09-18h)."""
    dia = timezone.localdate() + timedelta(days=dias)
    return timezone.make_aware(datetime.combine(dia, time(hora, minuto))).isoformat()


def verificar_sessao(client, telefone):
    """Simula o OTP por SMS ja verificado no wizard (sessao presa ao telefone)."""
    session = client.session
    session['otp_agendamento_telefone'] = telefone
    session['otp_agendamento_expira'] = (timezone.now() + timedelta(minutes=10)).isoformat()
    session.save()


@override_settings(RATELIMIT_ENABLE=False)
class ConfirmarAgendamentoTests(TestCase):
    def setUp(self):
        cache.clear()
        Feriado.objects.all().delete()  # feriados semeados podem cair na data do teste
        self.client = Client()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('120.00'))
        self.datetime_str = slot_local()
        self.url = reverse('aranha:confirmar_agendamento')

    def _post(self, with_otp=True, headers=None, **overrides):
        data = {
            'nome': 'Maria Teste',
            'telefone': '17999991111',
            'data_nascimento': '1990-06-15',
            'procedimento': self.proc.pk,
            'profissional': self.prof.pk,
            'datetime': self.datetime_str,
            'aceite_politica': 'on',
        }
        data.update(overrides)
        if with_otp:
            verificar_sessao(self.client, str(data['telefone']))
        return self.client.post(self.url, data, **(headers or {}))

    def test_cria_cliente_novo_e_atendimento(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        self.assertIn('sucesso', resp.url)
        self.assertEqual(Cliente.objects.filter(telefone='17999991111').count(), 1)
        self.assertEqual(Atendimento.objects.count(), 1)

        atd = Atendimento.objects.first()
        self.assertEqual(atd.status, 'PENDENTE')
        self.assertEqual(atd.cliente.nome, 'Maria Teste')
        self.assertEqual(atd.valor_cobrado, Decimal('120.00'))

    def test_sucesso_formata_preco_e_hora_local(self):
        self._post()
        dados = self.client.session.get('agendamento_sucesso')
        # a sessao e consumida so no GET da pagina de sucesso
        self.assertEqual(dados['valor'], 'R$ 120,00')
        self.assertIn('às 10:00', dados['data_hora'])
        self.assertTrue(dados['pendente'])

    def test_reutiliza_cliente_existente_e_atualiza_nome(self):
        Cliente.objects.create(nome='Antigo Nome', telefone='17999991111')
        self._post(nome='Nome Novo')

        clientes = Cliente.objects.filter(telefone='17999991111')
        self.assertEqual(clientes.count(), 1)
        self.assertEqual(clientes.first().nome, 'Nome Novo')

    def test_rejeita_quando_campos_faltando(self):
        resp = self._post(telefone='', with_otp=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_rejeita_conflito_de_horario(self):
        self._post()
        self.assertEqual(Atendimento.objects.count(), 1)

        # Tenta agendar exatamente no mesmo horario (outro cliente verificado)
        self._post(telefone='17999992222', nome='Outra Cliente')
        self.assertEqual(Atendimento.objects.count(), 1)  # Nao foi criado

    def test_get_redireciona_para_agendamento(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('agendamento', resp.url)

    def test_gera_token_de_cancelamento(self):
        self._post()
        atd = Atendimento.objects.first()
        self.assertIsNotNone(atd.token_cancelamento)
        self.assertEqual(len(atd.token_cancelamento), 43)  # token_urlsafe(32)

    # ─── OTP preso ao telefone (booking-02 / security-03) ───

    def test_cliente_novo_sem_otp_e_bloqueado(self):
        resp = self._post(with_otp=False)
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('sucesso', resp.url)
        self.assertEqual(Atendimento.objects.count(), 0)
        self.assertFalse(Cliente.objects.filter(telefone='17999991111').exists())

    def test_otp_de_outro_telefone_nao_libera_cadastro_da_vitima(self):
        vitima = Cliente.objects.create(
            nome='Vitima Silva', telefone='17912345678', email='vitima@example.com',
        )
        # Atacante verificou o PROPRIO celular e posta o telefone/e-mail da vitima
        verificar_sessao(self.client, '17900000001')
        resp = self._post(
            with_otp=False, telefone='17912345678', email='vitima@example.com', nome='Nome Trocado',
        )
        self.assertNotIn('sucesso', resp.url)
        vitima.refresh_from_db()
        self.assertEqual(vitima.nome, 'Vitima Silva')
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_pseudo_email_no_post_nao_e_identidade(self):
        vitima = Cliente.objects.create(nome='Vitima', telefone='17912345678')
        verificar_sessao(self.client, '17900000001')
        self._post(
            with_otp=False, telefone='17912345678',
            email='sms+17900000001@shivazen.local', nome='Nome Trocado',
        )
        vitima.refresh_from_db()
        self.assertEqual(vitima.nome, 'Vitima')
        self.assertIsNone(vitima.email)
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_email_de_outro_cadastro_e_recusado(self):
        Cliente.objects.create(nome='Dona do Email', telefone='17912345678', email='dona@example.com')
        resp = self._post(email='dona@example.com')
        self.assertNotIn('sucesso', resp.url)
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_sessao_expirada_exige_novo_otp(self):
        session = self.client.session
        session['otp_agendamento_telefone'] = '17999991111'
        session['otp_agendamento_expira'] = (timezone.now() - timedelta(minutes=1)).isoformat()
        session.save()
        self._post(with_otp=False)
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_sucesso_limpa_verificacao_da_sessao(self):
        self._post()
        self.assertNotIn('otp_agendamento_telefone', self.client.session)

    def test_telefone_com_ddi_e_normalizado(self):
        verificar_sessao(self.client, '17999991111')
        resp = self._post(with_otp=False, telefone='+55 (17) 99999-1111')
        self.assertIn('sucesso', resp.url)
        self.assertTrue(Cliente.objects.filter(telefone='17999991111').exists())

    # ─── Slot validado no servidor (booking-06 / crawl-09) ───

    def test_rejeita_horario_fora_do_expediente(self):
        resp = self._post(datetime=slot_local(hora=3))
        self.assertNotIn('sucesso', resp.url)
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_rejeita_minuto_quebrado(self):
        self._post(datetime=slot_local(hora=11, minuto=7))
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_rejeita_dia_de_folga(self):
        dia = timezone.localdate() + timedelta(days=3)
        ExcecaoDisponibilidade.objects.create(profissional=self.prof, data=dia, tipo='FOLGA')
        self._post()
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_rejeita_procedimento_inativo(self):
        self.proc.ativo = False
        self.proc.save()
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_rejeita_profissional_nao_habilitado(self):
        outro = criar_profissional(nome='Dra. Outra')
        self._post(profissional=outro.pk)
        self.assertEqual(Atendimento.objects.count(), 0)
        self.assertTrue(Habilitacao.objects.filter(profissional=self.prof).exists())

    # ─── Regra de bloqueio online (booking-07) ───

    def test_cliente_bloqueado_online_nao_agenda(self):
        Cliente.objects.create(nome='Faltosa', telefone='17999991111', bloqueado_online=True)
        resp = self._post()
        self.assertNotIn('sucesso', resp.url)
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_cliente_inativo_nao_e_reaproveitado(self):
        Cliente.objects.create(nome='Inativa', telefone='17999991111', ativo=False)
        self._post(nome='Outro Nome')
        cli = Cliente.objects.get(telefone='17999991111')
        self.assertEqual(cli.nome, 'Inativa')
        self.assertEqual(Atendimento.objects.count(), 0)

    # ─── Entradas malformadas -> redirect, nunca 500 (booking-12 / crawl-13) ───

    def test_entradas_malformadas_nao_geram_500(self):
        casos = [
            {'procedimento': 'abc'},
            {'procedimento': '999999'},
            {'profissional': '999999'},
            {'datetime': 'lixo'},
            {'datetime': '2030-01-01T10:00:00+99:00'},
            {'data_nascimento': '31/02/1990'},
        ]
        for extra in casos:
            with self.subTest(extra=extra):
                resp = self._post(**extra)
                self.assertEqual(resp.status_code, 302)
                self.assertNotIn('sucesso', resp.url)
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_erro_marca_reidratacao_do_wizard(self):
        self._post(with_otp=False)
        resp = self.client.get(reverse('aranha:agendamento_publico'))
        self.assertTrue(resp.context['rehidratar'])

    # ─── Anamnese (booking-10) ───

    def test_anamnese_obrigatoria_do_procedimento_e_exigida(self):
        FormularioAnamnese.objects.create(
            nome='Toxina', tipo='ANAMNESE', escopo='PROCEDIMENTO', procedimento=self.proc,
            obrigatorio=True, schema_json=[
                {'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante?', 'obrigatorio': True},
            ],
        )
        self._post(anamnese_respostas='')
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_anamnese_valida_e_gravada_e_formulario_de_outro_proc_ignorado(self):
        form = FormularioAnamnese.objects.create(
            nome='Toxina', tipo='ANAMNESE', escopo='PROCEDIMENTO', procedimento=self.proc,
            obrigatorio=True, schema_json=[
                {'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante?', 'obrigatorio': True},
                {'key': 'areas', 'tipo': 'checkboxes', 'label': 'Áreas', 'opcoes': ['Testa', 'Olhos']},
            ],
        )
        outro_proc = criar_procedimento(nome='Drenagem', profissional=self.prof)
        alheio = FormularioAnamnese.objects.create(
            nome='Outro', tipo='ANAMNESE', escopo='PROCEDIMENTO', procedimento=outro_proc,
            schema_json=[{'key': 'x', 'tipo': 'text', 'label': 'X'}],
        )
        respostas = {
            str(form.pk): {'gestante': 'nao', 'areas': ['Testa']},
            str(alheio.pk): {'x': 'valor'},
        }
        resp = self._post(anamnese_respostas=json.dumps(respostas), consent_dados_saude='on')
        self.assertIn('sucesso', resp.url)
        self.assertEqual(RespostaAnamnese.objects.count(), 1)
        ficha = RespostaAnamnese.objects.get()
        self.assertEqual(ficha.formulario, form)
        # bool normalizado ('nao' -> False) e ficha marcada como respondida
        self.assertEqual(ficha.respostas_json, {'gestante': False, 'areas': ['Testa']})
        self.assertIsNotNone(ficha.respondida_em)

    def test_wizard_nao_lista_pesquisa_pos_atendimento(self):
        FormularioAnamnese.objects.create(
            nome='Pesquisa NPS', tipo='PESQUISA', escopo='GLOBAL',
            schema_json=[{'key': 'nota', 'tipo': 'text', 'label': 'Nota'}],
        )
        resp = self.client.get(reverse('aranha:agendamento_publico'))
        nomes = [f['nome'] for f in resp.context['formularios_anamnese_data']]
        self.assertNotIn('Pesquisa NPS', nomes)
