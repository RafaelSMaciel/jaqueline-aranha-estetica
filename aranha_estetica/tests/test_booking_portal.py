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

    def test_resposta_pelo_link_audita_ip(self):
        from aranha_estetica.models import LogAuditoria
        at = _atendimento(self.cli, self.prof, self.proc, _local(2, 13))
        self._post(_notif(at), 'confirmar')
        log = LogAuditoria.objects.get(tabela='atendimento', registro_id=at.pk, acao__contains='link')
        self.assertEqual(log.ip_origem, '127.0.0.1')

    def test_token_de_nps_e_de_termo_nao_valem(self):
        at = _atendimento(self.cli, self.prof, self.proc, _local(2, 12))
        nps = _notif(at, tipo='NPS')
        termo = _notif(at, tipo='TERMO', canal='EMAIL')
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

    def _retorno_agendado(self):
        self.proc.exige_retorno = True
        self.proc.retorno_minimo_dias = 1
        self.proc.retorno_maximo_dias = 5
        self.proc.duracao_retorno_minutos = 15
        self.proc.save()
        origem = _atendimento(self.cli, self.prof, self.proc, _local(-10, 10), status='REALIZADO')
        inicio = _local(3, 10)
        return Atendimento.objects.create(
            cliente=self.cli, profissional=self.prof, procedimento=self.proc,
            data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=15),
            status='AGENDADO', valor_cobrado=Decimal('0'), valor_original=Decimal('0'),
            descricao_preco='Retorno obrigatorio (sem cobranca)',
            eh_retorno=True, atendimento_origem=origem,
        )

    def test_reagendar_retorno_mantem_vinculo_e_nao_gera_retorno_em_cadeia(self):
        retorno = self._retorno_agendado()
        resp = self._post(retorno, datetime=_local(4, 11).isoformat())
        self.assertIn('sucesso', resp.url)
        novo = Atendimento.objects.get(reagendado_de=retorno)
        self.assertTrue(novo.eh_retorno)
        self.assertEqual(novo.atendimento_origem, retorno.atendimento_origem)
        self.assertEqual(novo.valor_cobrado, Decimal('0'))
        # retorno ocupa a duracao de retorno, nao a do procedimento
        self.assertEqual(novo.data_hora_fim - novo.data_hora_inicio, timedelta(minutes=15))
        self.assertEqual(self.client.session['agendamento_sucesso']['valor'], 'Sem custo (retorno)')

        novo.marcar_realizado()
        self.assertFalse(Atendimento.objects.filter(atendimento_origem=novo).exists())

    def test_reagendar_leva_a_ficha_de_anamnese(self):
        from aranha_estetica.models import FormularioAnamnese, RespostaAnamnese
        at = _atendimento(self.cli, self.prof, self.proc, _local(3, 10))
        form = FormularioAnamnese.objects.create(
            nome='Ficha', tipo='ANAMNESE', schema_json=[{'key': 'alergias', 'tipo': 'text', 'label': 'Alergias'}],
        )
        ficha = RespostaAnamnese.objects.create(
            formulario=form, cliente=self.cli, atendimento=at,
            respostas_json={'alergias': 'Lidocaina'}, respondida_em=timezone.now(),
        )
        self._post(at, datetime=_local(4, 11).isoformat())
        novo = Atendimento.objects.get(reagendado_de=at)
        ficha.refresh_from_db()
        self.assertEqual(ficha.atendimento, novo)

    def test_reagendar_com_promocao_mostra_cheio_e_nome(self):
        from aranha_estetica.models import Promocao
        promo = Promocao.objects.create(
            procedimento=self.proc, nome='Glow', desconto_percentual=Decimal('20'),
            data_inicio=timezone.localdate(), data_fim=timezone.localdate() + timedelta(days=30),
        )
        at = _atendimento(self.cli, self.prof, self.proc, _local(3, 10))
        Atendimento.objects.filter(pk=at.pk).update(
            promocao=promo, valor_original=Decimal('150.00'), valor_cobrado=Decimal('120.00'),
        )
        self._post(at, datetime=_local(4, 11).isoformat())
        dados = self.client.session['agendamento_sucesso']
        self.assertEqual(dados['valor'], 'R$ 120,00')
        self.assertEqual(dados['valor_cheio'], 'R$ 150,00')
        self.assertEqual(dados['promocao'], 'Glow')


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

    def test_sem_resposta_conta_so_link_de_confirmacao_enviado(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from aranha_estetica.views import notificacoes

        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        at = _atendimento(criar_cliente(), prof, proc, _local(2, 10))
        _notif(at)  # lembrete WhatsApp enviado, sem resposta -> conta
        _notif(at, tipo='TERMO', canal='EMAIL')
        _notif(at, tipo='NPS')
        Notificacao.objects.create(atendimento=at, tipo='LEMBRETE', canal='WHATSAPP', status='FALHOU')

        request = RequestFactory().get('/painel/notificacoes/', {'status': 'pendente'})
        with patch('aranha_estetica.views.notificacoes.render', return_value=HttpResponse('ok')) as render:
            notificacoes.painel_notificacoes.__wrapped__(request)
        ctx = render.call_args.args[2]
        self.assertEqual(ctx['sem_resposta'], 1)
        self.assertEqual(len(list(ctx['notificacoes'])), 1)


@override_settings(RATELIMIT_ENABLE=False)
class ReagendarHorariosTests(TestCase):
    """rev_booking-09: a tela de reagendamento lista os mesmos horarios que o POST aceita."""

    def setUp(self):
        cache.clear()
        Feriado.objects.all().delete()
        self.prof = criar_profissional()
        self.proc60 = criar_procedimento(nome='Protocolo 60', duracao=60, profissional=self.prof)
        self.cli = criar_cliente()
        self.at = _atendimento(self.cli, self.prof, self.proc60, _local(3, 10))
        self.dia = timezone.localtime(self.at.data_hora_inicio).date().isoformat()

    def _horarios(self, token, **params):
        return self.client.get(reverse('aranha:reagendar_horarios', args=[token]), {'data': self.dia, **params})

    def test_proprio_horario_nao_conta_como_ocupado(self):
        resp = self._horarios(self.at.token_cancelamento)
        self.assertEqual(resp.status_code, 200)
        horarios = {h['horario']: [p['id'] for p in h['profissionais']] for h in resp.json()['horarios']}
        for hhmm in ('09:30', '10:00', '10:30'):
            with self.subTest(hhmm=hhmm):
                self.assertIn(self.prof.pk, horarios.get(hhmm, []))
        # O endpoint generico (sem o atendimento) continua vendo o horario ocupado
        geral = self.client.get(reverse('aranha:api_horarios_disponiveis'), {
            'data': self.dia, 'procedimento_id': self.proc60.pk,
        }).json()['horarios']
        self.assertNotIn('10:00', [h['horario'] for h in geral])
        # E o POST aceita o horario mostrado (paridade com a tela)
        resp = self.client.post(reverse('aranha:reagendar_agendamento', args=[self.at.token_cancelamento]), {
            'datetime': _local(3, 10, 30).isoformat(), 'profissional': self.prof.pk,
        })
        self.assertIn('sucesso', resp.url)

    def test_token_invalido_404_e_regras_do_reagendamento(self):
        self.assertEqual(self._horarios('token-que-nao-existe').status_code, 404)
        self.assertEqual(self._horarios(self.at.token_cancelamento, data='lixo').status_code, 400)
        self.cli.bloqueado_online = True
        self.cli.save()
        self.assertEqual(self._horarios(self.at.token_cancelamento).status_code, 400)

    def test_tela_usa_o_endpoint_do_proprio_link(self):
        html = self.client.get(
            reverse('aranha:reagendar_agendamento', args=[self.at.token_cancelamento])
        ).content.decode()
        self.assertIn(reverse('aranha:reagendar_horarios', args=[self.at.token_cancelamento]), html)
        self.assertNotIn(reverse('aranha:api_horarios_disponiveis'), html)


@override_settings(RATELIMIT_ENABLE=False)
class DigitosUnicodeTests(TestCase):
    """rev_booking-04: '²' passa em isdigit() mas int() explode -> nada de 500."""

    def setUp(self):
        cache.clear()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.dia = (timezone.localdate() + timedelta(days=3)).isoformat()

    def test_endpoints_publicos_nao_quebram(self):
        casos = [
            (reverse('aranha:api_horarios_disponiveis'), {'data': self.dia, 'procedimento_id': '²'}, 400),
            (reverse('aranha:api_dias_disponiveis'), {'mes': self.dia[:7], 'procedimento_id': '²'}, 400),
            (reverse('aranha:api_horarios_disponiveis'),
             {'data': self.dia, 'procedimento_id': self.proc.pk, 'profissional_id': '²'}, 200),
            (reverse('aranha:api_dias_disponiveis'),
             {'mes': self.dia[:7], 'procedimento_id': self.proc.pk, 'profissional_id': '①'}, 200),
        ]
        for url, params, status in casos:
            with self.subTest(params=params):
                self.assertEqual(self.client.get(url, params).status_code, status)
        pagina = self.client.get(reverse('aranha:agendamento_publico'), {'profissional': '²'})
        self.assertEqual(pagina.status_code, 200)
        self.assertEqual(pagina.context['prof_preselect'], '')

    def test_reagendar_com_profissional_unicode_volta_com_mensagem(self):
        at = _atendimento(criar_cliente(), self.prof, self.proc, _local(3, 10))
        resp = self.client.post(reverse('aranha:reagendar_agendamento', args=[at.token_cancelamento]), {
            'datetime': _local(4, 11).isoformat(), 'profissional': '²',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('sucesso', resp.url)


class IcsDescricaoTests(TestCase):
    """crawl-4: quebra de linha da DESCRIPTION escapada uma unica vez (RFC 5545)."""

    def test_description_sem_barra_duplicada(self):
        prof = criar_profissional(nome='Dra Ics Desc')
        proc = criar_procedimento(profissional=prof, nome='Peeling')
        _atendimento(criar_cliente(), prof, proc, _local(1, 10))
        resp = self.client.get(reverse('aranha:ics_feed_profissional', args=[prof.slug]), {'token': prof.ics_token})
        linha = next(ln for ln in resp.content.decode().split('\r\n') if ln.startswith('DESCRIPTION:'))
        # r'': barra+n LITERAL no .ics (escape RFC 5545), nunca barra dupla
        self.assertIn(r'Status: Agendado\nProcedimento: Peeling\nProfissional: Dra Ics Desc', linha)
        self.assertNotIn('\\\\', linha)


@override_settings(RATELIMIT_ENABLE=False)
class MeusAgendamentosMensagensTests(TestCase):
    """rev_booking-10 / rev_booking-02: sem codigo cru no toast; aviso de SMS fora."""

    def setUp(self):
        cache.clear()

    def test_toast_de_cancelamento_traduz_codigos_internos(self):
        cli = criar_cliente(telefone='17988880002')
        session = self.client.session
        session['meus_agendamentos_telefone'] = '17988880002'
        session.save()
        prof = criar_profissional()
        _atendimento(cli, prof, criar_procedimento(profissional=prof), _local(5, 10))
        html = self.client.get(reverse('aranha:meus_agendamentos')).content.decode()
        self.assertIn('MSGS_ERRO[res.data.erro]', html)
        self.assertNotIn('showToast(res.data.erro ||', html)

    def test_aviso_sem_sms_no_login_do_portal(self):
        url = reverse('aranha:meus_agendamentos')
        with patch('aranha_estetica.views.booking_reagendar.sms_disponivel', return_value=False):
            html = self.client.get(url).content.decode()
        self.assertIn('id="aviso-sms-indisponivel"', html)
        self.assertLess(html.index('id="aviso-sms-indisponivel"'), html.index('id="btn-enviar-codigo"'))
        with patch('aranha_estetica.views.booking_reagendar.sms_disponivel', return_value=True):
            self.assertNotIn('id="aviso-sms-indisponivel"', self.client.get(url).content.decode())
