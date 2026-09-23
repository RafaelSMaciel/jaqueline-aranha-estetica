"""Regressoes do portal do cliente: link /confirmar/, cancelar/reagendar por token,
Meus Agendamentos, feed ICS, embed e helpers do booking."""
import json
import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.forms import ClientePainelForm
from aranha_estetica.models import Atendimento, Feriado, Notificacao
from aranha_estetica.services.agendamento_service import AgendamentoService, formatar_brl

from .factories import criar_cliente, criar_procedimento, criar_profissional


def _local(dias, hora, minuto=0):
    dia = timezone.localdate() + timedelta(days=dias)
    return timezone.make_aware(datetime.combine(dia, time(hora, minuto)))


def _atendimento(cliente, prof, proc, inicio, status='AGENDADO'):
    return Atendimento.objects.create(
        cliente=cliente, profissional=prof, procedimento=proc,
        data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=proc.duracao_minutos),
        status=status, valor_cobrado=Decimal('89.90'),
    )


def _notif(atendimento, tipo='LEMBRETE', canal='WHATSAPP'):
    return Notificacao.objects.create(
        atendimento=atendimento, tipo=tipo, canal=canal, status='ENVIADO',
        token=secrets.token_urlsafe(32),
    )


@override_settings(RATELIMIT_ENABLE=False)
class ConfirmarPresencaLinkTests(TestCase):
    """security-06 / admin_views-05 / booking-08: token certo + FSM."""

    def setUp(self):
        cache.clear()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()

    def _post(self, notif, acao):
        return self.client.post(reverse('aranha:confirmar_presenca', args=[notif.token]), {'acao': acao})

    def test_confirma_agendado_futuro(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(2, 10))
        notif = _notif(at)
        resp = self._post(notif, 'confirmar')
        self.assertEqual(resp.status_code, 200)
        at.refresh_from_db()
        notif.refresh_from_db()
        self.assertEqual(at.status, 'CONFIRMADO')
        self.assertEqual(notif.resposta, 'CONFIRMOU')

    def test_cancela_pela_fsm(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(2, 11))
        self._post(_notif(at), 'cancelar')
        at.refresh_from_db()
        self.assertEqual(at.status, 'CANCELADO')

    def test_token_de_nps_e_de_termo_nao_valem(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(2, 12))
        nps = _notif(at, tipo='NPS')
        termo = _notif(at, tipo='LEMBRETE', canal='EMAIL')
        self.assertEqual(self._post(nps, 'cancelar').status_code, 404)
        self.assertEqual(self._post(termo, 'confirmar').status_code, 404)
        at.refresh_from_db()
        self.assertEqual(at.status, 'AGENDADO')

    def test_nao_ressuscita_cancelado_nem_cancela_realizado(self):
        for status, acao in (('CANCELADO', 'confirmar'), ('REAGENDADO', 'confirmar'),
                             ('REALIZADO', 'cancelar')):
            with self.subTest(status=status):
                at = _atendimento(self.cli, self.prof, self.proc, _local(3, 9), status=status)
                resp = self._post(_notif(at), acao)
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.context['nao_editavel'])
                at.refresh_from_db()
                self.assertEqual(at.status, status)
                at.delete()

    def test_pendente_nao_e_autoaprovado(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(2, 14), status='PENDENTE')
        self._post(_notif(at), 'confirmar')
        at.refresh_from_db()
        self.assertEqual(at.status, 'PENDENTE')

    def test_atendimento_passado_nao_muda(self):
        at = _atendimento(self.cli, self.prof, self.proc, timezone.now() - timedelta(hours=3))
        self._post(_notif(at), 'cancelar')
        at.refresh_from_db()
        self.assertEqual(at.status, 'AGENDADO')


@override_settings(RATELIMIT_ENABLE=False)
class CancelarPorTokenTests(TestCase):
    """booking-26 / booking-12: FSM + corpo malformado -> 400."""

    def setUp(self):
        cache.clear()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()
        self.url = reverse('aranha:cancelar_agendamento')

    def _post(self, corpo):
        return self.client.post(self.url, data=corpo, content_type='application/json')

    def test_cancela_pendente(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(2, 10), status='PENDENTE')
        resp = self._post(json.dumps({'token': at.token_cancelamento}))
        self.assertEqual(resp.status_code, 200)
        at.refresh_from_db()
        self.assertEqual(at.status, 'CANCELADO')

    def test_reagendado_nao_pode_ser_cancelado(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(2, 10), status='REAGENDADO')
        resp = self._post(json.dumps({'token': at.token_cancelamento}))
        self.assertEqual(resp.status_code, 400)
        at.refresh_from_db()
        self.assertEqual(at.status, 'REAGENDADO')

    def test_corpo_malformado_retorna_400(self):
        for corpo in ('[1]', '"x"', json.dumps({'token': 5}), 'nao-json'):
            with self.subTest(corpo=corpo):
                self.assertIn(self._post(corpo).status_code, (400, 404))


@override_settings(RATELIMIT_ENABLE=False)
class ReagendarTests(TestCase):
    """booking-06/21 + security-17: slot validado, sem troca silenciosa, FSM."""

    def setUp(self):
        cache.clear()
        Feriado.objects.all().delete()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()

    def _post(self, at, **dados):
        return self.client.post(reverse('aranha:reagendar_agendamento', args=[at.token_cancelamento]), dados)

    def test_reagenda_para_slot_valido_mantendo_aprovacao(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(3, 10))
        resp = self._post(at, datetime=_local(4, 11).isoformat(), profissional=self.prof.pk)
        self.assertIn('sucesso', resp.url)
        at.refresh_from_db()
        self.assertEqual(at.status, 'REAGENDADO')
        novo = Atendimento.objects.get(reagendado_de=at)
        self.assertEqual(novo.status, 'AGENDADO')
        self.assertEqual(novo.profissional, self.prof)

    def test_pendente_continua_pendente(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(3, 10), status='PENDENTE')
        self._post(at, datetime=_local(4, 11).isoformat())
        novo = Atendimento.objects.get(reagendado_de=at)
        self.assertEqual(novo.status, 'PENDENTE')

    def test_troca_de_profissional_volta_para_aprovacao(self):
        outra = criar_profissional(nome='Dra. Outra')
        from aranha_estetica.models import Habilitacao
        Habilitacao.objects.create(profissional=outra, procedimento=self.proc)
        at = _atendimento(self.cli, self.prof, self.proc, _local(3, 10))
        self._post(at, datetime=_local(4, 11).isoformat(), profissional=outra.pk)
        novo = Atendimento.objects.get(reagendado_de=at)
        self.assertEqual(novo.status, 'PENDENTE')

    def test_rejeita_horario_fora_do_expediente(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(3, 10))
        resp = self._post(at, datetime=_local(4, 3).isoformat())
        self.assertNotIn('sucesso', resp.url)
        at.refresh_from_db()
        self.assertEqual(at.status, 'AGENDADO')
        self.assertFalse(Atendimento.objects.filter(reagendado_de=at).exists())

    def test_mesmo_horario_do_antigo_e_permitido(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(3, 10))
        # 10:00 do mesmo dia: o proprio slot antigo nao bloqueia
        resp = self._post(at, datetime=_local(3, 10).isoformat())
        self.assertIn('sucesso', resp.url)

    def test_entradas_malformadas(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(3, 10))
        for dados in ({'datetime': 'lixo'}, {'datetime': _local(4, 11).isoformat(), 'profissional': 'abc'}):
            with self.subTest(dados=dados):
                resp = self._post(at, **dados)
                self.assertEqual(resp.status_code, 302)
                self.assertNotIn('sucesso', resp.url)

    def test_cliente_bloqueado_online(self):
        self.cli.bloqueado_online = True
        self.cli.save()
        at = _atendimento(self.cli, self.prof, self.proc, _local(3, 10))
        self._post(at, datetime=_local(4, 11).isoformat())
        self.assertFalse(Atendimento.objects.filter(reagendado_de=at).exists())

    def test_get_passa_data_minima_local(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(3, 10))
        resp = self.client.get(reverse('aranha:reagendar_agendamento', args=[at.token_cancelamento]))
        self.assertEqual(resp.context['data_min'], (timezone.localdate() + timedelta(days=1)).isoformat())


@override_settings(RATELIMIT_ENABLE=False)
class MeusAgendamentosListaTests(TestCase):
    """booking-22 / booking-30: acoes p/ PENDENTE e sem innerHTML com dado do banco."""

    def setUp(self):
        cache.clear()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, nome='<img src=x onerror=alert(1)>')
        self.cli = criar_cliente(telefone='17988880001')
        session = self.client.session
        session['meus_agendamentos_telefone'] = '17988880001'
        session.save()

    def test_pendente_tem_cancelar_e_reagendar(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(5, 10), status='PENDENTE')
        html = self.client.get(reverse('aranha:meus_agendamentos')).content.decode()
        self.assertIn(reverse('aranha:reagendar_agendamento', args=[at.token_cancelamento]), html)
        self.assertIn(f'data-token="{at.token_cancelamento}"', html)

    def test_menos_de_24h_nao_oferece_reagendar(self):
        at = _atendimento(self.cli, self.prof, self.proc, timezone.now() + timedelta(hours=5))
        html = self.client.get(reverse('aranha:meus_agendamentos')).content.decode()
        self.assertNotIn(reverse('aranha:reagendar_agendamento', args=[at.token_cancelamento]), html)
        self.assertIn('Faltam menos de 24h', html)

    def test_nome_do_procedimento_nao_vai_para_innerhtml(self):
        _atendimento(self.cli, self.prof, self.proc, _local(5, 10))
        html = self.client.get(reverse('aranha:meus_agendamentos')).content.decode()
        self.assertNotIn("innerHTML = '<strong>' + nome", html)
        self.assertNotIn('<img src=x', html)  # autoescape no atributo
        self.assertNotIn('spinner-border', html)


class IcsFeedTests(TestCase):
    """crawl-06 / pgtests-04: timezone.utc removido no Django 5."""

    def test_feed_com_atendimento_retorna_vevent(self):
        prof = criar_profissional(nome='Dra Ics')
        proc = criar_procedimento(profissional=prof)
        _atendimento(criar_cliente(), prof, proc, _local(1, 10))
        url = reverse('aranha:ics_feed_profissional', args=[prof.slug])
        resp = self.client.get(url, {'token': prof.ics_token})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp['Content-Type'].startswith('text/calendar'))
        self.assertIn('BEGIN:VEVENT', resp.content.decode())
        self.assertEqual(self.client.get(url, {'token': 'errado'}).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 404)


@override_settings(RATELIMIT_ENABLE=False)
class LinksEPaginasTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_agendar_por_profissional_preseleciona(self):
        prof = criar_profissional(nome='Dra Link')
        resp = self.client.get(reverse('aranha:agendar_por_profissional', args=[prof.slug]),
                               {'procedimento': '5&x=1'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f'profissional={prof.pk}', resp.url)
        self.assertNotIn('x=1', resp.url)
        pagina = self.client.get(resp.url)
        self.assertEqual(pagina.context['prof_preselect'], str(prof.pk))

    def test_preselect_invalido_e_ignorado(self):
        pagina = self.client.get(reverse('aranha:agendamento_publico'),
                                 {'profissional': 'abc', 'procedimento': '"><script>'})
        self.assertEqual(pagina.context['prof_preselect'], '')
        self.assertEqual(pagina.context['proc_preselect'], '')

    def test_preco_do_card_nao_e_localizado(self):
        prof = criar_profissional()
        criar_procedimento(profissional=prof, preco=Decimal('89.90'))
        html = self.client.get(reverse('aranha:agendamento_publico')).content.decode()
        self.assertIn('data-proc-preco="89.9"', html)

    def test_embed_sem_cdn_e_com_marca(self):
        html = self.client.get(reverse('aranha:embed_agendar')).content.decode()
        self.assertNotIn('bootstrap', html)
        self.assertNotIn('font-awesome', html)
        self.assertNotIn('class="fas', html)
        self.assertIn('Powered by', html)


class HelpersBookingTests(TestCase):
    def test_formatar_brl(self):
        self.assertEqual(formatar_brl(Decimal('89.9')), 'R$ 89,90')
        self.assertEqual(formatar_brl(1500), 'R$ 1.500,00')
        self.assertEqual(formatar_brl(None), '')

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_aprovar_envia_email_com_hora_local(self):
        """booking-19: dt do banco vem em UTC — e-mail precisa mostrar 10:00 local."""
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cli = criar_cliente(email='cli@example.com')
        at = _atendimento(cli, prof, proc, _local(2, 10), status='PENDENTE')
        at = Atendimento.objects.select_related('cliente', 'procedimento', 'profissional').get(pk=at.pk)
        with patch.object(AgendamentoService, '_enviar_email_confirmacao') as enviar, \
                self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(AgendamentoService().aprovar(at))
        dados = enviar.call_args.args[1]
        self.assertIn('às 10:00', dados['data_hora'])
        self.assertEqual(dados['valor'], 'R$ 89,90')

    def test_cliente_painel_form_normaliza_e_valida(self):
        outro = criar_cliente(email='dono@example.com')
        cli = criar_cliente(consent_whatsapp_nps=True)
        base = {'nome': 'Nova', 'telefone': '+55 (17) 99999-0001', 'email': '', 'cpf': '',
                'ativo': '1', 'aceita_comunicacao': '1'}
        form = ClientePainelForm(base, instance=cli)
        self.assertTrue(form.is_valid(), form.errors)
        salvo = form.save()
        self.assertEqual(salvo.telefone, '17999990001')
        self.assertIsNone(salvo.email)
        self.assertTrue(salvo.consent_whatsapp_nps)  # consent fora do form fica intacto

        form = ClientePainelForm({**base, 'cpf': '111.111.111-11'}, instance=cli)
        self.assertFalse(form.is_valid())
        self.assertIn('cpf', form.errors)

        form = ClientePainelForm({**base, 'email': outro.email.upper()}, instance=cli)
        self.assertFalse(form.is_valid())


class PainelNotificacoesTests(TestCase):
    """admin_templates-23 / admin_views-34 (parte da view): enviadas/falhas e tipos."""

    def test_contexto_separa_enviadas_e_falhas(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from aranha_estetica.views import notificacoes

        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        at = _atendimento(criar_cliente(), prof, proc, _local(2, 10))
        _notif(at)
        Notificacao.objects.create(atendimento=at, tipo='NPS', canal='WHATSAPP', status='FALHOU')

        request = RequestFactory().get('/painel/notificacoes/')
        view = getattr(notificacoes.painel_notificacoes, '__wrapped__', None)
        self.assertIsNotNone(view, 'staff_required deve preservar __wrapped__ (functools.wraps)')
        with patch('aranha_estetica.views.notificacoes.render', return_value=HttpResponse('ok')) as render:
            view(request)
        ctx = render.call_args.args[2]
        self.assertEqual(ctx['total'], 2)
        self.assertEqual(ctx['enviadas'], 1)
        self.assertEqual(ctx['falhas'], 1)
        self.assertEqual(ctx['tipos'], Notificacao.TIPO_CHOICES)
        # nunca enviada (enviado_em NULL) no fim da lista
        self.assertIsNone(list(ctx['notificacoes'])[-1].enviado_em)
