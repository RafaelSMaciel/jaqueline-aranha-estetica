"""Canais de notificacao (e-mail/WhatsApp/push) e jobs: falha fechada, hora
local, SITE_URL em tempo de chamada, consentimento e janelas do NPS."""
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import (
    Atendimento, AvaliacaoNPS, CompraPacote, Configuracao, LogAuditoria, Notificacao,
    Usuario,
)
from aranha_estetica.utils import email as email_utils
from aranha_estetica.utils import whatsapp as wa_utils

from .factories import (
    criar_atendimento, criar_cliente, criar_compra_pacote, criar_pacote,
    criar_procedimento, criar_profissional,
)

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'
CONSOLE = 'django.core.mail.backends.console.EmailBackend'


def _as_14h_local(dias=1):
    """Datetime aware em UTC (como volta do banco) de 14:00 hora local."""
    local = timezone.localtime(timezone.now() + timedelta(days=dias)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )
    return local.astimezone(dt_timezone.utc)


# ─── E-mail ───────────────────────────────────────────────────────────
@override_settings(EMAIL_BACKEND=LOCMEM, SITE_URL='https://clinica.example.com')
class EmailFalhaFechadaTests(TestCase):

    @override_settings(EMAIL_BACKEND=CONSOLE, DEBUG=False)
    def test_backend_console_fora_de_debug_retorna_false(self):
        ok = email_utils.enviar_cancelamento_email('ana@example.com', {'nome': 'Ana'})
        self.assertFalse(ok)
        self.assertFalse(email_utils.email_configurado())

    def test_marketing_sem_token_de_descadastro_nao_sai(self):
        ok = email_utils.enviar_aniversario_email('ana@example.com', {'nome': 'Ana', 'desconto': 15})
        self.assertFalse(ok)
        self.assertEqual(len(mail.outbox), 0)

    def test_marketing_leva_list_unsubscribe_com_site_url_atual(self):
        ok = email_utils.enviar_aniversario_email(
            'ana@example.com', {'nome': 'Ana', 'desconto': 15}, unsub_token='tok123',
        )
        self.assertTrue(ok)
        msg = mail.outbox[0]
        self.assertIn('https://clinica.example.com/lgpd/unsubscribe/tok123/', msg.extra_headers['List-Unsubscribe'])
        self.assertEqual(msg.extra_headers['List-Unsubscribe-Post'], 'List-Unsubscribe=One-Click')
        self.assertIn('https://clinica.example.com/lgpd/unsubscribe/tok123/', msg.alternatives[0][0])
        self.assertIn('aniversário', msg.subject)

    def test_log_nao_expoe_email_em_claro(self):
        with self.assertLogs('aranha_estetica.utils.email', level='INFO') as logs:
            email_utils.enviar_cancelamento_email('fulana.silva@example.com', {'nome': 'Ana'})
        for record in logs.records:
            self.assertNotEqual(getattr(record, 'destinatario', ''), 'fulana.silva@example.com')
            self.assertNotIn('fulana.silva@', getattr(record, 'destinatario', ''))

    def test_send_email_async_eager_nao_faz_retry_sincrono(self):
        from aranha_estetica.tasks import send_email_async
        with patch.object(email_utils, 'enviar_cancelamento_email', return_value=False) as fake:
            resultado = send_email_async.apply(args=('enviar_cancelamento_email', 'a@example.com', {}))
        self.assertEqual(fake.call_count, 1)
        self.assertFalse(resultado.result)

    def test_aniversario_job_envia_com_descadastro(self):
        from aranha_estetica.tasks import job_aniversario_clientes
        hoje = timezone.localdate()
        cli = criar_cliente(
            nome='Aniversariante', email='niver@example.com',
            data_nascimento=hoje.replace(year=1992), consent_email_marketing=True,
        )
        job_aniversario_clientes.apply()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(cli.token_descadastro, mail.outbox[0].extra_headers['List-Unsubscribe'])


# ─── WhatsApp ─────────────────────────────────────────────────────────
@override_settings(DEBUG=False, SITE_URL='https://clinica.example.com')
class WhatsAppTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional(nome='Dra. Jaqueline')
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente(nome='Bia', consent_whatsapp_confirmacao=True)

    @patch.dict('os.environ', {'WHATSAPP_TOKEN': '', 'WHATSAPP_PHONE_ID': ''})
    def test_sem_token_nao_marca_enviado(self):
        self.assertFalse(wa_utils.enviar_template_whatsapp('17999990000', 'x'))
        atd = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_as_14h_local())
        notif = wa_utils.enviar_confirmacao_d1(atd)
        self.assertEqual(notif.status, 'FALHOU')

    @patch.dict('os.environ', {'WHATSAPP_TOKEN': '', 'WHATSAPP_PHONE_ID': ''})
    def test_job_d1_sem_canal_nao_cria_notificacao(self):
        from aranha_estetica.tasks import job_enviar_lembrete_dia_seguinte
        criar_atendimento(self.cli, self.prof, self.proc, data_hora=_as_14h_local())
        job_enviar_lembrete_dia_seguinte.apply()
        self.assertFalse(Notificacao.objects.exists())

    @patch.dict('os.environ', {'WHATSAPP_TOKEN': 'tok', 'WHATSAPP_PHONE_ID': '123'})
    def test_d1_usa_hora_local_e_site_url(self):
        atd = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_as_14h_local())
        atd = Atendimento.objects.select_related('cliente', 'profissional', 'procedimento').get(pk=atd.pk)
        with patch.object(wa_utils, 'enviar_template_whatsapp', return_value=True) as fake:
            notif = wa_utils.enviar_confirmacao_d1(atd)
        params = [p['text'] for p in fake.call_args[0][2][0]['parameters']]
        self.assertEqual(params[2], '14:00')
        self.assertTrue(params[5].startswith('https://clinica.example.com/confirmar/'))
        self.assertEqual(notif.status, 'ENVIADO')
        self.assertNotIn('Bia', notif.mensagem)

    @patch.dict('os.environ', {'WHATSAPP_TOKEN': 'tok', 'WHATSAPP_PHONE_ID': '123'})
    def test_job_d1_respeita_consentimento(self):
        from aranha_estetica.tasks import job_enviar_lembrete_dia_seguinte
        sem_consent = criar_cliente(nome='Sem', consent_whatsapp_confirmacao=False)
        criar_atendimento(sem_consent, self.prof, self.proc, data_hora=_as_14h_local())
        with patch.object(wa_utils, 'enviar_template_whatsapp', return_value=True) as fake:
            job_enviar_lembrete_dia_seguinte.apply()
        fake.assert_not_called()


# ─── NPS ──────────────────────────────────────────────────────────────
class NpsCandidatosTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente(consent_whatsapp_nps=True)

    def _realizado(self, dias_atras):
        inicio = timezone.now() - timedelta(days=dias_atras)
        atd = criar_atendimento(self.cli, self.prof, self.proc, data_hora=inicio, status='REALIZADO')
        return atd

    def test_nps_falhou_mais_lembrete_enviado_e_retentado(self):
        from aranha_estetica.tasks import candidatos_nps
        atd = self._realizado(2)
        Notificacao.objects.create(atendimento=atd, tipo='NPS', status='FALHOU', token='t1')
        Notificacao.objects.create(atendimento=atd, tipo='LEMBRETE', status='ENVIADO', token='t2')
        self.assertEqual(list(candidatos_nps().values_list('pk', flat=True)), [atd.pk])

    def test_nps_enviado_nao_repete(self):
        from aranha_estetica.tasks import candidatos_nps
        atd = self._realizado(2)
        Notificacao.objects.create(atendimento=atd, tipo='NPS', status='ENVIADO', token='t1')
        self.assertFalse(candidatos_nps().exists())

    def test_historico_antigo_fora_da_janela(self):
        from aranha_estetica.tasks import candidatos_nps
        self._realizado(30)
        self.assertFalse(candidatos_nps().exists())

    def test_limite_de_falhas(self):
        from aranha_estetica.tasks import candidatos_nps
        atd = self._realizado(2)
        for i in range(3):
            Notificacao.objects.create(atendimento=atd, tipo='NPS', status='FALHOU', token=f'f{i}')
        self.assertFalse(candidatos_nps().exists())


# ─── Jobs em hora local ───────────────────────────────────────────────
class JobsHoraLocalTests(TestCase):
    def test_expirar_pacotes_as_22h30_nao_expira_o_ultimo_dia(self):
        """22:30 BRT = 01:30 UTC do dia seguinte: o ultimo dia ainda vale."""
        from aranha_estetica.tasks import job_expirar_pacotes
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        pc = criar_compra_pacote(criar_cliente(), criar_pacote(procedimento=proc, sessoes=2))
        CompraPacote.objects.filter(pk=pc.pk).update(data_expiracao=datetime(2026, 9, 19).date())
        agora_utc = datetime(2026, 9, 20, 1, 30, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=agora_utc):
            job_expirar_pacotes.apply()
        pc.refresh_from_db()
        self.assertEqual(pc.status, 'ATIVO')

    def test_limpeza_nao_lanca_falta_automatica(self):
        from aranha_estetica.tasks import job_limpeza_status_atendimentos
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cli = criar_cliente()
        passado = timezone.now() - timedelta(days=3)
        agendado = criar_atendimento(cli, prof, proc, data_hora=passado, status='AGENDADO')
        pendente = criar_atendimento(cli, prof, proc, data_hora=passado + timedelta(hours=2), status='PENDENTE')
        job_limpeza_status_atendimentos.apply()
        agendado.refresh_from_db()
        pendente.refresh_from_db()
        cli.refresh_from_db()
        self.assertEqual(agendado.status, 'AGENDADO')
        self.assertEqual(pendente.status, 'CANCELADO')
        self.assertEqual(cli.faltas_consecutivas, 0)


# ─── Alerta detrator ──────────────────────────────────────────────────
@override_settings(EMAIL_BACKEND=LOCMEM)
class AlertaDetratorTests(TestCase):
    def setUp(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        atd = criar_atendimento(criar_cliente(), prof, proc, status='REALIZADO')
        self.nps = AvaliacaoNPS.objects.create(atendimento=atd, nota=3)

    def test_chave_em_maiusculas_da_tela_de_config_funciona(self):
        from aranha_estetica.tasks import job_alerta_detrator_nps
        Configuracao.objects.create(chave='EMAIL_ADMIN', valor='dona@example.com')
        job_alerta_detrator_nps.apply()
        self.assertEqual(mail.outbox[0].to, ['dona@example.com'])
        self.nps.refresh_from_db()
        self.assertTrue(self.nps.alerta_enviado)

    @override_settings(EMAIL_BACKEND=CONSOLE, DEBUG=False)
    def test_sem_backend_real_nao_marca_enviado(self):
        from aranha_estetica.tasks import job_alerta_detrator_nps
        Configuracao.objects.create(chave='email_admin', valor='dona@example.com')
        job_alerta_detrator_nps.apply()
        self.nps.refresh_from_db()
        self.assertFalse(self.nps.alerta_enviado)


# ─── Auditoria e push ─────────────────────────────────────────────────
class AuditoriaIpTests(TestCase):
    def test_registrar_log_grava_ip_do_request(self):
        from aranha_estetica.utils.audit import registrar_log
        request = RequestFactory().get('/', REMOTE_ADDR='203.0.113.7')
        request.user = None
        registrar_log(None, 'teste ip', 'cliente', 1, request=request)
        self.assertEqual(LogAuditoria.objects.get(acao='teste ip').ip_origem, '203.0.113.7')


class WebPushPayloadTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user(email='push@test.com', password='x-senha-123', nome='P')
        self.client.force_login(self.user)

    def test_payload_nao_objeto_devolve_400(self):
        for corpo in ('[]', 'null', '"x"'):
            resp = self.client.post(
                reverse('aranha:webpush_unsubscribe'), data=corpo, content_type='application/json',
            )
            self.assertEqual(resp.status_code, 400, corpo)

    def test_endpoint_nao_https_devolve_400(self):
        corpo = json.dumps({'endpoint': 'http://169.254.169.254/x', 'keys': {'p256dh': 'a', 'auth': 'b'}})
        resp = self.client.post(
            reverse('aranha:webpush_subscribe'), data=corpo, content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
