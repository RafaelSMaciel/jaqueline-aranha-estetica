"""Termos e consentimento (LGPD): prova do aceite, pagina /termo/<token>/,
aceite no agendamento, Django admin, compliance e lembrete D-1.

Regressao de gap1_termos_consentimento_lgpd (a suite nao tinha nenhum teste de
termo/aceite: token de qualquer tipo, "sucesso" sem gravar, user-agent vazio,
texto aceito editavel e booking sem aceite passavam verdes).
"""
import hashlib
import json
import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib import admin as dj_admin
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import (
    AceiteTermo,
    Atendimento,
    Cliente,
    Feriado,
    FormularioAnamnese,
    LogAuditoria,
    Notificacao,
    RespostaAnamnese,
    Usuario,
    VersaoTermo,
)
from aranha_estetica.utils.datas import hoje

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_procedimento,
    criar_profissional,
)

UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) teste-termos'


def _sha(texto):
    return hashlib.sha256(texto.encode('utf-8')).hexdigest()


def _versao(tipo='LGPD', procedimento=None, versao='1.0', conteudo=None):
    """Versao ATIVA nova (arquiva a vigente do mesmo escopo: 1 ativa por escopo)."""
    VersaoTermo.objects.filter(tipo=tipo, procedimento=procedimento, ativa=True).update(ativa=False)
    return VersaoTermo.objects.create(
        tipo=tipo, procedimento=procedimento, titulo=f'Termo {tipo} v{versao}',
        conteudo=conteudo or f'Texto integral do termo {tipo} v{versao}.',
        versao=versao, vigente_desde=timezone.localdate(), ativa=True,
    )


def _local(dias, hora=10):
    return timezone.make_aware(datetime.combine(hoje() + timedelta(days=dias), time(hora, 0)))


# ─── Modelo: prova do aceite e imutabilidade ─────────────────────────
class AceiteTermoModeloTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente()
        self.lgpd = _versao()

    def _request(self):
        return RequestFactory().post('/x/', REMOTE_ADDR='200.10.20.30', HTTP_USER_AGENT=UA)

    def test_registrar_grava_ip_user_agent_e_hash_do_texto(self):
        aceite = AceiteTermo.registrar(self.cliente, self.lgpd, self._request())
        self.assertEqual(aceite.ip, '200.10.20.30')
        self.assertEqual(aceite.user_agent, UA)
        self.assertEqual(aceite.conteudo_sha256, _sha(self.lgpd.conteudo))

    def test_registrar_e_idempotente_e_mantem_a_prova_do_1o_aceite(self):
        primeiro = AceiteTermo.registrar(self.cliente, self.lgpd, self._request())
        outro = RequestFactory().post('/x/', REMOTE_ADDR='10.0.0.1', HTTP_USER_AGENT='outro')
        segundo = AceiteTermo.registrar(self.cliente, self.lgpd, outro)
        self.assertEqual(primeiro.pk, segundo.pk)
        self.assertEqual(AceiteTermo.objects.count(), 1)
        self.assertEqual(AceiteTermo.objects.get().user_agent, UA)

    def test_sem_versao_vigente_nao_grava(self):
        self.assertIsNone(AceiteTermo.registrar(self.cliente, None, self._request()))
        self.assertFalse(AceiteTermo.objects.exists())

    def test_lgpd_vigente(self):
        self.assertEqual(VersaoTermo.lgpd_vigente(), self.lgpd)
        VersaoTermo.objects.filter(pk=self.lgpd.pk).update(ativa=False)
        self.assertIsNone(VersaoTermo.lgpd_vigente())

    def test_versao_aceita_nao_muda_o_texto(self):
        AceiteTermo.registrar(self.cliente, self.lgpd, self._request())
        self.lgpd.conteudo = 'TEXTO ADULTERADO'
        with self.assertRaises(ValidationError):
            self.lgpd.full_clean()
        with self.assertRaises(ValidationError):
            self.lgpd.save()
        self.lgpd.refresh_from_db()
        self.assertNotEqual(self.lgpd.conteudo, 'TEXTO ADULTERADO')

    def test_versao_aceita_pode_ser_desativada(self):
        AceiteTermo.registrar(self.cliente, self.lgpd, self._request())
        self.lgpd.ativa = False
        self.lgpd.save()
        self.lgpd.refresh_from_db()
        self.assertFalse(self.lgpd.ativa)

    def test_versao_sem_aceite_continua_editavel(self):
        self.lgpd.conteudo = 'Texto corrigido antes de qualquer aceite'
        self.lgpd.full_clean()
        self.lgpd.save()


# ─── Pagina publica do termo (/termo/<token>/) ───────────────────────
@override_settings(RATELIMIT_ENABLE=False)
class TermoAssinaturaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_USER_AGENT=UA)
        self.cliente = criar_cliente()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.lgpd = _versao()
        self.termo_proc = _versao('PROCEDIMENTO', self.proc)
        self.at = criar_atendimento(self.cliente, self.prof, self.proc, data_hora=_local(3))
        self.notif = self._notif('TERMO', 'EMAIL', 'tok-termo')

    def _notif(self, tipo, canal, token, atendimento=None):
        return Notificacao.objects.create(
            atendimento=atendimento or self.at, tipo=tipo, canal=canal, token=token,
        )

    def _url(self, token='tok-termo'):
        return reverse('aranha:termo_assinatura', args=[token])

    def _post_tudo(self, token='tok-termo'):
        dados = {f'aceite_{t.pk}': '1' for t in (self.lgpd, self.termo_proc)}
        return self.client.post(self._url(token), dados, REMOTE_ADDR='200.1.2.3')

    def test_get_mostra_os_termos_pendentes(self):
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.termo_proc.titulo)
        self.assertContains(r, self.lgpd.titulo)

    def test_token_de_nps_ou_lembrete_nao_abre_o_termo(self):
        self._notif('NPS', 'WHATSAPP', 'tok-nps')
        self._notif('LEMBRETE', 'WHATSAPP', 'tok-d1')
        for token in ('tok-nps', 'tok-d1', 'nao-existe'):
            with self.subTest(token=token):
                self.assertEqual(self.client.get(self._url(token)).status_code, 404)
                self.assertEqual(self._post_tudo(token).status_code, 404)
        self.assertFalse(AceiteTermo.objects.exists())

    def test_post_parcial_recusa_e_nao_grava_nada(self):
        r = self.client.post(self._url(), {f'aceite_{self.lgpd.pk}': '1'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(AceiteTermo.objects.exists())

    def test_post_vazio_recusa_e_nao_grava_nada(self):
        r = self.client.post(self._url(), {})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(AceiteTermo.objects.exists())

    def test_post_completo_grava_prova_e_auditoria(self):
        r = self._post_tudo()
        self.assertEqual(r.status_code, 200)
        aceites = AceiteTermo.objects.filter(cliente=self.cliente)
        self.assertEqual(
            set(aceites.values_list('versao_termo_id', flat=True)),
            {self.lgpd.pk, self.termo_proc.pk},
        )
        for aceite in aceites.select_related('versao_termo'):
            self.assertEqual(aceite.ip, '200.1.2.3')
            self.assertEqual(aceite.user_agent, UA)
            self.assertEqual(aceite.conteudo_sha256, _sha(aceite.versao_termo.conteudo))
            self.assertEqual(aceite.atendimento_id, self.at.pk)
        self.assertTrue(
            LogAuditoria.objects.filter(tabela='aceite_termo', registro_id=self.cliente.pk).exists()
        )

    def test_atendimento_cancelado_ou_realizado_nao_aceita(self):
        for status in ('CANCELADO', 'REALIZADO'):
            with self.subTest(status=status):
                Atendimento.objects.filter(pk=self.at.pk).update(status=status)
                self.assertEqual(self.client.get(self._url()).status_code, 410)
                self.assertEqual(self._post_tudo().status_code, 410)
        self.assertFalse(AceiteTermo.objects.exists())

    def test_atendimento_ja_encerrado_nao_aceita(self):
        inicio = timezone.now() - timedelta(hours=3)
        Atendimento.objects.filter(pk=self.at.pk).update(
            data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=30),
        )
        self.assertEqual(self._post_tudo().status_code, 410)
        self.assertFalse(AceiteTermo.objects.exists())

    def test_link_antigo_vale_ate_o_atendimento(self):
        # link enviado ha 8 dias p/ atendimento daqui a 30: antes dava 'expirado' (7d fixos)
        Atendimento.objects.filter(pk=self.at.pk).update(
            data_hora_inicio=_local(30), data_hora_fim=_local(30) + timedelta(minutes=30),
        )
        Notificacao.objects.filter(pk=self.notif.pk).update(
            criado_em=timezone.now() - timedelta(days=8),
        )
        self.assertEqual(self.client.get(self._url()).status_code, 200)
        self.assertEqual(self._post_tudo().status_code, 200)
        self.assertEqual(AceiteTermo.objects.count(), 2)


# ─── Django admin: prova somente leitura ─────────────────────────────
class AdminTermosTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email='admin-termos@test.com', password='senha-forte-123', nome='Admin Termos',
        )
        self.request = RequestFactory().get('/django-admin-sv/')
        self.request.user = self.admin
        self.versao = _versao()
        self.versao_admin = dj_admin.site._registry[VersaoTermo]
        self.aceite_admin = dj_admin.site._registry[AceiteTermo]

    def _aceitar(self):
        return AceiteTermo.registrar(criar_cliente(), self.versao)

    def test_versao_sem_aceite_editavel(self):
        ro = self.versao_admin.get_readonly_fields(self.request, self.versao)
        self.assertNotIn('conteudo', ro)
        self.assertTrue(self.versao_admin.has_delete_permission(self.request, self.versao))

    def test_versao_com_aceite_so_permite_desativar(self):
        self._aceitar()
        ro = self.versao_admin.get_readonly_fields(self.request, self.versao)
        for campo in ('tipo', 'procedimento', 'titulo', 'conteudo', 'versao'):
            self.assertIn(campo, ro)
        self.assertNotIn('ativa', ro)
        self.assertFalse(self.versao_admin.has_delete_permission(self.request, self.versao))

    def test_aceite_nao_e_criado_alterado_nem_apagado_no_admin(self):
        aceite = self._aceitar()
        self.assertFalse(self.aceite_admin.has_add_permission(self.request))
        self.assertFalse(self.aceite_admin.has_change_permission(self.request, aceite))
        self.assertFalse(self.aceite_admin.has_delete_permission(self.request, aceite))


# ─── Agendamento publico: aceite e consentimentos ────────────────────
@override_settings(RATELIMIT_ENABLE=False)
class BookingConsentimentoTests(TestCase):
    TELEFONE = '17999994444'

    def setUp(self):
        cache.clear()
        Feriado.objects.all().delete()
        self.client = Client(HTTP_USER_AGENT=UA)
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('120.00'))
        self.lgpd = _versao()

    def _post(self, **overrides):
        data = {
            'nome': 'Maria Consentimento',
            'telefone': self.TELEFONE,
            'data_nascimento': '1990-06-15',
            'procedimento': self.proc.pk,
            'profissional': self.prof.pk,
            'datetime': _local(3).isoformat(),
            'aceite_politica': 'on',
        }
        data.update(overrides)
        data = {k: v for k, v in data.items() if v is not None}
        session = self.client.session  # OTP por SMS ja validado no wizard
        session['otp_agendamento_telefone'] = self.TELEFONE
        session['otp_agendamento_expira'] = (timezone.now() + timedelta(minutes=10)).isoformat()
        session.save()
        return self.client.post(reverse('aranha:confirmar_agendamento'), data)

    def _anamnese(self):
        form = FormularioAnamnese.objects.create(
            nome='Toxina', tipo='ANAMNESE', escopo='PROCEDIMENTO', procedimento=self.proc,
            obrigatorio=True, schema_json=[
                {'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante?', 'obrigatorio': True},
            ],
        )
        return json.dumps({str(form.pk): {'gestante': 'nao'}})

    def test_sem_aceite_da_politica_nada_e_gravado(self):
        self._post(aceite_politica=None)
        self.assertFalse(Cliente.objects.filter(telefone=self.TELEFONE).exists())
        self.assertFalse(Atendimento.objects.exists())
        self.assertFalse(AceiteTermo.objects.exists())

    def test_com_aceite_grava_aceite_lgpd_com_prova(self):
        self._post()
        at = Atendimento.objects.get()
        aceite = AceiteTermo.objects.get(cliente=at.cliente, versao_termo=self.lgpd)
        self.assertEqual(aceite.user_agent, UA)
        self.assertEqual(aceite.conteudo_sha256, _sha(self.lgpd.conteudo))
        self.assertTrue(aceite.ip)

    def test_anamnese_sem_consentimento_de_saude_nao_grava(self):
        self._post(anamnese_respostas=self._anamnese())
        self.assertFalse(RespostaAnamnese.objects.exists())
        self.assertFalse(Atendimento.objects.exists())
        self.assertFalse(Cliente.objects.filter(telefone=self.TELEFONE).exists())

    def test_anamnese_com_consentimento_de_saude_grava(self):
        self._post(anamnese_respostas=self._anamnese(), consent_dados_saude='on')
        self.assertEqual(RespostaAnamnese.objects.count(), 1)
        self.assertEqual(Atendimento.objects.count(), 1)

    def test_termo_do_procedimento_exigido_e_gravado_com_o_atendimento(self):
        termo = _versao('PROCEDIMENTO', self.proc)
        self._post()
        self.assertFalse(Atendimento.objects.exists())

        self._post(**{f'aceite_termo_{termo.pk}': 'on'})
        at = Atendimento.objects.get()
        aceite = AceiteTermo.objects.get(versao_termo=termo)
        self.assertEqual(aceite.atendimento_id, at.pk)
        self.assertEqual(aceite.user_agent, UA)

    def test_booking_nao_cria_lembrete_fantasma(self):
        # A notificacao do termo era gravada como LEMBRETE/EMAIL/PENDENTE, criada
        # ate sem e-mail, e inflava 'sem resposta' no painel.
        self._post(email='maria@exemplo.com.br')
        self.assertEqual(Atendimento.objects.count(), 1)
        self.assertFalse(Notificacao.objects.filter(tipo='LEMBRETE').exists())

    def test_nenhum_consentimento_de_comunicacao_pre_marcado(self):
        html = self.client.get(reverse('aranha:agendamento_publico')).content.decode()
        inputs = re.findall(r'<input[^>]*name="consent_[a-z_]+"[^>]*>', html)
        self.assertTrue(inputs)
        for tag in inputs:
            self.assertNotRegex(tag, r'\bchecked\b', tag)


# ─── Lembrete D-1 nao e suprimido pelo link do termo ─────────────────
class LembreteD1ComTermoTests(TestCase):
    def test_notificacao_de_termo_enviada_nao_bloqueia_o_d1(self):
        from aranha_estetica.tasks import job_enviar_lembrete_dia_seguinte

        cliente = criar_cliente(consent_whatsapp_confirmacao=True)
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        at = criar_atendimento(cliente, prof, proc, data_hora=_local(1))
        for canal in ('EMAIL', 'WHATSAPP'):
            Notificacao.objects.create(
                atendimento=at, tipo='TERMO', canal=canal, status='ENVIADO',
                token=f'tok-termo-{canal}', enviado_em=timezone.now(),
            )

        enviada = mock.Mock(status='ENVIADO')
        with mock.patch('aranha_estetica.utils.whatsapp.pode_enviar_whatsapp', return_value=True), \
                mock.patch('aranha_estetica.utils.whatsapp.enviar_confirmacao_d1',
                           return_value=enviada) as envio:
            job_enviar_lembrete_dia_seguinte.apply()
        envio.assert_called_once()
        self.assertEqual(envio.call_args.args[0].pk, at.pk)


# ─── Compliance de termos ────────────────────────────────────────────
class ComplianceTermosTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email='admin-compliance@test.com', password='senha-forte-123', nome='Admin',
        )
        self.client.force_login(self.admin)
        self.lgpd = _versao()

    def test_aceite_de_inativo_nao_compensa_pendencia_de_ativo(self):
        for _ in range(3):
            AceiteTermo.registrar(criar_cliente(ativo=False), self.lgpd)
        assinou = criar_cliente()
        AceiteTermo.registrar(assinou, self.lgpd)
        criar_cliente()  # ativa sem aceite
        ativos = Cliente.objects.filter(ativo=True).count()

        r = self.client.get(reverse('aranha:admin_termos_compliance'))
        self.assertEqual(r.status_code, 200)
        linha = next(x for x in r.context['resumo_versoes'] if x['versao'].pk == self.lgpd.pk)
        self.assertEqual(linha['relevantes'], ativos)
        self.assertEqual(linha['assinados'], 1)
        self.assertEqual(linha['pendentes'], ativos - 1)
        self.assertGreaterEqual(linha['pendentes'], 1)
        self.assertLess(linha['pct'], 100)
