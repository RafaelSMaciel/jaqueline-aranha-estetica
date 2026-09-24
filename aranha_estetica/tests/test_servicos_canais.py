"""Canais de notificacao (e-mail/WhatsApp/push) e jobs: falha fechada, hora
local, SITE_URL em tempo de chamada, consentimento e janelas do NPS."""
import json
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import Mock, patch

from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

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
RESEND = 'anymail.backends.resend.EmailBackend'


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
        ok = email_utils.enviar_aniversario_email('ana@example.com', {'nome': 'Ana'})
        self.assertFalse(ok)
        self.assertEqual(len(mail.outbox), 0)

    def test_marketing_leva_list_unsubscribe_com_site_url_atual(self):
        ok = email_utils.enviar_aniversario_email(
            'ana@example.com', {'nome': 'Ana'}, unsub_token='tok123',
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

    def test_aniversario_nao_promete_desconto_sem_mecanismo(self):
        """gap3-03: nenhum fluxo aplica o '15%' — o e-mail so felicita."""
        from aranha_estetica.tasks import job_aniversario_clientes
        hoje = timezone.localdate()
        criar_cliente(
            nome='Aniversariante', email='niver@example.com',
            data_nascimento=hoje.replace(year=1990), consent_email_marketing=True,
        )
        job_aniversario_clientes.apply()
        msg = mail.outbox[0]
        html = msg.alternatives[0][0]
        texto = strip_tags(re.sub(r'<style.*?</style>', '', html, flags=re.S))
        self.assertIsNone(re.search(r'\d\s*%', texto))
        for promessa in ('desconto', 'Desconto', 'cupom', 'Válido por 7 dias', 'presente'):
            self.assertNotIn(promessa, texto)
            self.assertNotIn(promessa, msg.subject)
        self.assertIn('/agendamento/', html)

    @override_settings(DEBUG=False, EMAIL_BACKEND='django.core.mail.backends.filebased.EmailBackend')
    def test_filebased_fora_de_debug_nao_conta_como_configurado(self):
        """gap4-06: arquivo no disco efemero do container nao entrega nada."""
        self.assertFalse(email_utils.email_configurado())
        self.assertFalse(email_utils.enviar_cancelamento_email('ana@example.com', {'nome': 'Ana'}))

    @override_settings(DEBUG=False)
    def test_locmem_do_test_runner_segue_configurado(self):
        self.assertTrue(email_utils.email_configurado())

    # Railway Hobby bloqueia SMTP: o provedor HTTP (anymail) e a entrega real
    @override_settings(DEBUG=False, EMAIL_BACKEND=RESEND,
                       ANYMAIL={'RESEND_API_KEY': 're_teste', 'REQUESTS_TIMEOUT': 7},
                       DEFAULT_FROM_EMAIL='Clinica <contato@clinica.example.com>')
    def test_anymail_com_chave_entrega_pela_api_http(self):
        resposta = Mock(status_code=200, text='{"id": "re-msg-1"}')
        resposta.json.return_value = {'id': 're-msg-1'}
        self.assertTrue(email_utils.email_configurado())
        with patch('requests.Session.request', return_value=resposta) as http:
            ok = email_utils.enviar_cancelamento_email('ana@example.com', {'nome': 'Ana'})
        self.assertTrue(ok)
        http.assert_called_once()
        req = http.call_args.kwargs
        self.assertEqual((req['method'], req['url']), ('POST', 'https://api.resend.com/emails'))
        self.assertEqual(req['headers']['Authorization'], 'Bearer re_teste')
        corpo = json.loads(req['data'])
        self.assertEqual(corpo['to'], ['ana@example.com'])
        self.assertEqual(corpo['from'], 'Clinica <contato@clinica.example.com>')
        self.assertEqual(req['timeout'], 7)  # settings: REQUESTS_TIMEOUT = EMAIL_TIMEOUT (nao 30 s)

    @override_settings(DEBUG=False, EMAIL_BACKEND=RESEND, ANYMAIL={})
    def test_anymail_sem_chave_falha_fechado_sem_chamar_a_api(self):
        with patch('requests.Session.request') as http:
            ok = email_utils.enviar_cancelamento_email('ana@example.com', {'nome': 'Ana'})
        self.assertFalse(ok)
        self.assertFalse(email_utils.email_configurado())
        http.assert_not_called()

    @override_settings(DEBUG=False, EMAIL_BACKEND=RESEND, ANYMAIL={'RESEND_API_KEY': 're_teste'})
    def test_anymail_erro_da_api_nao_quebra_o_fluxo(self):
        resposta = Mock(status_code=403, text='{"message": "domain is not verified"}')
        resposta.json.return_value = {'message': 'domain is not verified'}
        with patch('requests.Session.request', return_value=resposta), \
                self.assertLogs('aranha_estetica.utils.email', level='ERROR') as logs:
            ok = email_utils.enviar_cancelamento_email('ana@example.com', {'nome': 'Ana'})
        self.assertFalse(ok)
        self.assertEqual(logs.records[0].getMessage(), 'email_falha_envio')

    def test_promocao_sem_cupom_e_validade_limitada_ao_fim_da_promo(self):
        """gap3-02: validade anunciada nunca passa de promo.data_fim; cupom nao sai."""
        from aranha_estetica.tasks import job_promocao_mensal, validade_promocao
        fim = timezone.localdate() + timedelta(days=10)
        self.assertEqual(validade_promocao(30, fim.isoformat()), fim.strftime('%d/%m/%Y'))
        self.assertEqual(
            validade_promocao(5, fim), (timezone.localdate() + timedelta(days=5)).strftime('%d/%m/%Y'),
        )
        criar_cliente(nome='Promo', email='promo@example.com', consent_email_marketing=True)
        job_promocao_mensal.apply(
            args=('Setembro Glow', '<p>Oferta</p>'),
            kwargs={'cupom': 'VIP15', 'validade_dias': 60, 'data_fim': fim.isoformat()},
        )
        corpo = mail.outbox[0].alternatives[0][0]
        self.assertNotIn('VIP15', corpo)
        self.assertIn(f'Oferta válida até {fim.strftime("%d/%m/%Y")}', corpo)

    def test_email_de_termo_informa_procedimento_data_e_validade(self):
        ok = email_utils.enviar_termos_pendentes_email('ana@example.com', {
            'nome': 'Ana', 'link_termo': 'https://clinica.example.com/termo/tok/',
            'procedimento': 'Limpeza de Pele', 'data_hora': '20/10/2026 14:00',
            'valido_ate': '20/10/2026 14:00',
        })
        self.assertTrue(ok)
        corpo = mail.outbox[0].alternatives[0][0]
        for trecho in ('Limpeza de Pele', '20/10/2026 14:00', 'Este link vale até', '/termo/tok/'):
            self.assertIn(trecho, corpo)

    def test_job_pacote_expirando_usa_o_saldo_da_compra(self):
        from aranha_estetica.tasks import job_verificar_pacotes_expirando
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cli = criar_cliente(nome='Pacoteira', email='pacote@example.com')
        compra = criar_compra_pacote(cli, criar_pacote(procedimento=proc, sessoes=4))
        validade = timezone.localdate() + timedelta(days=7)
        CompraPacote.objects.filter(pk=compra.pk).update(data_expiracao=validade)
        atd = criar_atendimento(cli, prof, proc)
        atd.status = 'REALIZADO'
        atd.save()

        job_verificar_pacotes_expirando.apply()

        self.assertEqual(len(mail.outbox), 1)
        corpo = mail.outbox[0].alternatives[0][0]
        self.assertIn('>3<', corpo)
        self.assertIn(f'Sessões realizadas até {validade.strftime("%d/%m/%Y")} contam', corpo)

    def test_pacote_expirando_explica_que_sessao_ate_a_validade_conta(self):
        ok = email_utils.enviar_pacote_expirando_email('ana@example.com', {
            'nome': 'Ana', 'pacote': 'Glow', 'dias': 1, 'sessoes_restantes': 2,
            'valido_ate': '30/09/2026',
        })
        self.assertTrue(ok)
        self.assertIn('Sessões realizadas até 30/09/2026 contam', mail.outbox[0].alternatives[0][0])


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
    def test_job_d1_envia_mesmo_com_termo_ja_enviado_por_email(self):
        """gap1-05: notificacao do termo (e-mail) nao conta como D-1 enviado."""
        from aranha_estetica.tasks import job_enviar_lembrete_dia_seguinte
        atd = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_as_14h_local())
        Notificacao.objects.create(atendimento=atd, tipo='TERMO', canal='EMAIL', status='ENVIADO', token='t1')
        Notificacao.objects.create(atendimento=atd, tipo='LEMBRETE', canal='EMAIL', status='ENVIADO', token='t2')
        with patch.object(wa_utils, 'enviar_template_whatsapp', return_value=True) as fake:
            job_enviar_lembrete_dia_seguinte.apply()
        fake.assert_called_once()
        self.assertTrue(Notificacao.objects.filter(
            atendimento=atd, tipo='LEMBRETE', canal='WHATSAPP', status='ENVIADO',
        ).exists())

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


def _token_totp(device):
    import time
    from django_otp.oath import TOTP
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time()
    return f'{totp.token():0{device.digits}d}'


@override_settings(ADMIN_2FA_OBRIGATORIO=False)
class WebPush2FATests(TestCase):
    """rev_security-08: sessao so com senha (TOTP pendente) nao assina push."""

    CORPO = json.dumps({
        'endpoint': 'https://push.example.com/atacante',
        'keys': {'p256dh': 'chave', 'auth': 'segredo'},
    })

    def setUp(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice
        prof = criar_profissional()
        self.user = Usuario.objects.create_user(
            email='prof2fa@test.com', password='x-senha-123', nome='Dra. 2FA',
            papel=Usuario.PAPEL_PROFISSIONAL, profissional=prof,
        )
        self.device = TOTPDevice.objects.create(user=self.user, name='totp', confirmed=True)

    def _subscribe(self):
        return self.client.post(
            reverse('aranha:webpush_subscribe'), data=self.CORPO, content_type='application/json',
        )

    def test_sessao_sem_desafio_recebe_403_e_nao_cria_assinatura(self):
        from aranha_estetica.models import AssinaturaPush
        self.client.force_login(self.user)
        resp = self._subscribe()
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(AssinaturaPush.objects.exists())

    def test_sessao_verificada_assina(self):
        from aranha_estetica.models import AssinaturaPush
        self.client.force_login(self.user)
        self.client.post(reverse('aranha:admin_2fa_verify'), {'token': _token_totp(self.device)})
        resp = self._subscribe()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(AssinaturaPush.objects.filter(user=self.user).exists())

    def test_usuario_sem_totp_continua_assinando(self):
        outro = Usuario.objects.create_user(email='semtotp@test.com', password='x-senha-123', nome='S')
        self.client.force_login(outro)
        self.assertEqual(self._subscribe().status_code, 200)


# ─── Lista de espera: aviso de vaga sem vazar o nome ─────────────────
@override_settings(EMAIL_BACKEND=LOCMEM, SITE_URL='https://clinica.example.com')
class ListaEsperaAvisoPrivacidadeTests(TestCase):
    """followups-lista-espera-vaza-nome / rev_security-01: e-mail digitado no
    form anonimo nao recebe o nome cadastrado do dono do telefone."""

    def setUp(self):
        from aranha_estetica.utils.datas import data_local
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.vitima = criar_cliente(
            nome='Valeria Vitima Sobrenome', telefone='17911112222', email='vitima@example.com',
        )
        self.slot = criar_atendimento(criar_cliente(nome='Outra Pessoa'), self.prof, self.proc)
        self.dia = data_local(self.slot.data_hora_inicio)

    def _inscrever(self, email_contato):
        from aranha_estetica.models import ListaEspera
        return ListaEspera.objects.create(
            cliente=self.vitima, procedimento=self.proc, data_desejada=self.dia,
            email_contato=email_contato,
        )

    def _cancelar_slot(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.slot.cancelar(motivo='teste')

    @staticmethod
    def _corpos(msg):
        return [msg.body, *[conteudo for conteudo, _tipo in msg.alternatives]]

    def test_email_digitado_por_terceiro_nao_recebe_nome_do_cadastro(self):
        self._inscrever('atacante@example.com')
        self._cancelar_slot()
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['atacante@example.com'])
        for corpo in self._corpos(msg):
            self.assertNotIn('Valeria', corpo)
        self.assertIn('Boa notícia!', msg.alternatives[0][0])

    def test_email_do_proprio_cadastro_recebe_o_nome(self):
        self._inscrever('VITIMA@example.com')
        self._cancelar_slot()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Valeria Vitima Sobrenome', mail.outbox[0].alternatives[0][0])

    def test_sem_email_de_contato_usa_o_do_cadastro_com_nome(self):
        self._inscrever(None)
        self._cancelar_slot()
        self.assertEqual(mail.outbox[0].to, ['vitima@example.com'])
        self.assertIn('Valeria Vitima Sobrenome', mail.outbox[0].alternatives[0][0])

    def test_nome_para_helper(self):
        from aranha_estetica.services.lista_espera_service import _nome_para
        espera = self._inscrever('atacante@example.com')
        self.assertEqual(_nome_para(espera, 'atacante@example.com'), '')
        self.assertEqual(_nome_para(espera, ' Vitima@Example.com '), 'Valeria Vitima Sobrenome')
        espera.cliente.email = None
        self.assertEqual(_nome_para(espera, 'atacante@example.com'), '')


class FilaEsperaTemplateTests(TestCase):
    """crawl-6: template renderiza sem site_url/link no contexto (preview do painel)."""

    def test_renderiza_so_com_dados(self):
        from django.template.loader import render_to_string
        html = render_to_string('email/fila_espera.html', {
            'dados': {'procedimento': 'Limpeza de Pele', 'data': '10/10/2026'},
        })
        self.assertIn('Boa notícia!', html)
        self.assertIn('/agendamento/', html)
        self.assertNotIn('href=""', html)

    def test_link_explicito_tem_prioridade(self):
        from django.template.loader import render_to_string
        link = 'https://clinica.example.com/agendamento/?procedimento=3'
        html = render_to_string('email/fila_espera.html', {
            'site_url': 'https://clinica.example.com',
            'dados': {'nome': 'Ana', 'procedimento': 'X', 'link': link},
        })
        self.assertIn(f'href="{link}"', html)
        self.assertIn('Boa notícia, Ana!', html)


# ─── DSAR: cooldown e quota antes de gerar o codigo ──────────────────
@override_settings(RATELIMIT_ENABLE=False)
class DsarOtpQuotaTests(TestCase):
    """rev_security-05 (parte DSAR): pedido sem SMS saindo nao invalida o codigo vigente."""

    TEL = '17933335555'

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        criar_cliente(nome='Titular DSAR', telefone=self.TEL)
        self.url = reverse('aranha:lgpd_meus_dados')

    def _post(self):
        with patch('aranha_estetica.views.lgpd.sms_disponivel', return_value=True):
            return self.client.post(self.url, {'telefone': self.TEL})

    def test_quota_do_telefone_esgotada_nao_invalida_codigo_vigente(self):
        from django.core.cache import cache
        from aranha_estetica.models import CodigoOtp
        from aranha_estetica.utils import sms
        codigo, obj = CodigoOtp.gerar_sms(self.TEL, proposito=CodigoOtp.PROPOSITO_DSAR)
        # fora da janela de reenvio (60s): so a quota decide
        CodigoOtp.objects.filter(pk=obj.pk).update(criado_em=timezone.now() - timedelta(minutes=5))
        cache.set(f'sms_rl:tel:{sms.formatar_telefone(self.TEL)}', sms.SMS_MAX_POR_HORA, 3600)
        self.assertEqual(self._post().status_code, 200)
        self.assertEqual(CodigoOtp.objects.filter(proposito=CodigoOtp.PROPOSITO_DSAR).count(), 1)
        ok, _motivo = CodigoOtp.verificar_sms(self.TEL, codigo, proposito=CodigoOtp.PROPOSITO_DSAR)
        self.assertTrue(ok)

    def test_pedido_repetido_dentro_do_cooldown_nao_gera_outro_codigo(self):
        from aranha_estetica.models import CodigoOtp
        codigo, _obj = CodigoOtp.gerar_sms(self.TEL, proposito=CodigoOtp.PROPOSITO_DSAR)
        self._post()
        self.assertEqual(CodigoOtp.objects.filter(proposito=CodigoOtp.PROPOSITO_DSAR).count(), 1)
        ok, _motivo = CodigoOtp.verificar_sms(self.TEL, codigo, proposito=CodigoOtp.PROPOSITO_DSAR)
        self.assertTrue(ok)

    def test_fora_do_cooldown_e_com_quota_gera_codigo(self):
        from aranha_estetica.models import CodigoOtp
        self._post()
        self.assertEqual(CodigoOtp.objects.filter(proposito=CodigoOtp.PROPOSITO_DSAR).count(), 1)
