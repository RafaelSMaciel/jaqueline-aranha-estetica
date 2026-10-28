"""Consentimentos com historico (spec padrao-sql §4.c, migration 0050).

Servico (append-only, no-op, ultimo registro manda, empate por id, anotacao de
querysets), anonimizacao (historico fica sem IP/UA) e regressao dos fluxos que
enviam marketing/WhatsApp: cada um so sai com o par vigente concedido —
revogado (descadastro/painel/esquecimento) nao recebe; reconsentimento volta a
receber.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import skipUnless
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from aranha_estetica.models import Atendimento, Cliente, Consentimento, ListaEspera, Notificacao
from aranha_estetica.services import consentimentos
from aranha_estetica.services.lgpd import LgpdService
from aranha_estetica.utils import whatsapp as wa_utils

from .factories import (
    conceder, criar_atendimento, criar_cliente, criar_link, criar_procedimento, criar_profissional,
)

MKT = consentimentos.MARKETING_EMAIL
NPS = consentimentos.PESQUISA_WHATSAPP
D1 = consentimentos.LEMBRETE_WHATSAPP
LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def _as_14h_local(dias=1):
    local = timezone.localtime(timezone.now() + timedelta(days=dias)).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )
    return local.astimezone(dt_timezone.utc)


class ServicoConsentimentoTests(TestCase):
    def setUp(self):
        self.cli = criar_cliente()

    def _registrar(self, par, concedido, **kw):
        kw.setdefault('origem', 'AGENDAMENTO_ONLINE')
        return consentimentos.registrar(self.cli, *par, concedido, **kw)

    def test_sem_linha_e_nao_concedido(self):
        self.assertEqual(set(consentimentos.estado_atual(self.cli).values()), {False})
        self.assertFalse(consentimentos.vigente(self.cli, *MKT))
        # revogar o que nunca foi concedido: nada a registrar
        self.assertIsNone(self._registrar(MKT, False))
        self.assertFalse(Consentimento.objects.exists())

    def test_append_only_concede_revoga_reconcede_e_ultimo_manda(self):
        req = RequestFactory().post('/', HTTP_USER_AGENT='UA teste', REMOTE_ADDR='10.0.0.5')
        primeira = self._registrar(MKT, True, texto='Aceito promoções', request=req)
        self.assertIsNone(self._registrar(MKT, True))  # no-op: estado ja e esse
        self._registrar(MKT, False, origem='DESCADASTRO')
        self.assertFalse(consentimentos.vigente(self.cli, *MKT))
        self._registrar(MKT, True)
        self.assertTrue(consentimentos.vigente(self.cli, *MKT))
        self.assertEqual(
            list(Consentimento.objects.filter(cliente=self.cli).order_by('id').values_list('concedido', 'origem')),
            [(True, 'AGENDAMENTO_ONLINE'), (False, 'DESCADASTRO'), (True, 'AGENDAMENTO_ONLINE')],
        )
        primeira.refresh_from_db()
        self.assertEqual((primeira.ip_origem, primeira.user_agent, primeira.texto),
                         ('10.0.0.5', 'UA teste', 'Aceito promoções'))
        # os outros pares nao sao afetados
        self.assertEqual(consentimentos.estado_atual(self.cli), {MKT: True, NPS: False, D1: False})

    def test_empate_de_registrado_em_desempata_pelo_id(self):
        instante = timezone.now() - timedelta(days=1)
        self._registrar(NPS, True, registrado_em=instante)
        self._registrar(NPS, False, registrado_em=instante)
        self.assertFalse(consentimentos.vigente(self.cli, *NPS))
        anotado = consentimentos.anotar_vigente(Cliente.objects.filter(pk=self.cli.pk), *NPS, caminho='pk').get()
        self.assertFalse(anotado.tem_consentimento)

    def test_registro_mais_antigo_inserido_depois_nao_vence_o_mais_recente(self):
        self._registrar(D1, True)
        self._registrar(D1, False, registrado_em=timezone.now() - timedelta(days=30), origem='MIGRACAO')
        self.assertTrue(consentimentos.vigente(self.cli, *D1))

    def test_par_e_origem_invalidos(self):
        with self.assertRaises(ValueError):
            consentimentos.registrar(self.cli, 'MARKETING', 'WHATSAPP', True, origem='PAINEL')
        with self.assertRaises(ValueError):
            consentimentos.registrar(self.cli, *MKT, True, origem='QUALQUER')

    def test_linha_e_imutavel_no_python(self):
        linha = self._registrar(MKT, True)
        linha.concedido = False
        with self.assertRaises(ValidationError):
            linha.save()
        with self.assertRaises(ValidationError):
            linha.delete()
        with self.assertRaises(ValidationError):
            Consentimento.objects.filter(pk=linha.pk).delete()
        self.assertTrue(Consentimento.objects.get(pk=linha.pk).concedido)

    def test_anotar_vigente_em_atendimentos_e_clientes(self):
        outro = criar_cliente()
        self._registrar(D1, True)
        consentimentos.registrar(outro, *D1, True, origem='PAINEL', texto='pedido por telefone')
        consentimentos.registrar(outro, *D1, False, origem='PAINEL')
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        a1 = criar_atendimento(self.cli, prof, proc)
        criar_atendimento(outro, prof, proc)
        ids = consentimentos.com_consentimento(Atendimento.objects.all(), *D1).values_list('pk', flat=True)
        self.assertEqual(list(ids), [a1.pk])
        clientes = consentimentos.com_consentimento(Cliente.objects.all(), *D1, caminho='pk')
        self.assertEqual(list(clientes), [self.cli])

    @skipUnless(connection.vendor == 'postgresql', 'sp_anonimizar_cliente so no Postgres')
    def test_esquecer_mantem_historico_sem_ip_ua_e_revoga(self):
        req = RequestFactory().post('/', HTTP_USER_AGENT='UA', REMOTE_ADDR='10.0.0.7')
        self._registrar(MKT, True, request=req)
        self._registrar(D1, True, request=req)
        self._registrar(NPS, True, request=req)
        self._registrar(NPS, False, request=req, origem='DESCADASTRO')
        LgpdService.esquecer_cliente(self.cli)
        linhas = Consentimento.objects.filter(cliente=self.cli)
        self.assertEqual(linhas.count(), 6)  # 4 + revogacao de MKT e D1 (NPS ja revogado)
        self.assertEqual(set(linhas.filter(origem='ESQUECIMENTO').values_list('finalidade', flat=True)),
                         {'MARKETING', 'LEMBRETE_AGENDAMENTO'})
        self.assertEqual(set(linhas.values_list('ip_origem', 'user_agent')), {(None, '')})
        self.assertEqual(set(consentimentos.estado_atual(self.cli).values()), {False})

    def test_dsar_exporta_estado_e_historico(self):
        self._registrar(MKT, True, texto='Aceito promoções')
        self._registrar(MKT, False, origem='DESCADASTRO')
        dados = LgpdService.exportar_dados_cliente(self.cli)['consentimentos']
        self.assertIn({'finalidade': 'MARKETING', 'canal': 'EMAIL', 'concedido': False}, dados['vigentes'])
        self.assertEqual([(h['concedido'], h['origem']) for h in dados['historico']],
                         [(False, 'DESCADASTRO'), (True, 'AGENDAMENTO_ONLINE')])
        self.assertEqual(dados['historico'][1]['texto'], 'Aceito promoções')


# ─── Regressao: so envia com o par vigente concedido ───────────────────
@override_settings(DEBUG=False, SITE_URL='https://clinica.example.com')
@patch.dict('os.environ', {'WHATSAPP_TOKEN': 'tok', 'WHATSAPP_PHONE_ID': '123'})
class LembreteD1Tests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)

    def _enviar(self):
        from aranha_estetica.jobs import job_lembrete_diario
        with patch.object(wa_utils, 'enviar_template', return_value=True) as fake:
            job_lembrete_diario()
        return fake

    def test_so_quem_autorizou_e_nao_revogou(self):
        autorizou = criar_cliente(nome='Autorizou', consent_whatsapp_confirmacao=True)
        revogou = criar_cliente(nome='Revogou', consent_whatsapp_confirmacao=True)
        consentimentos.revogar(revogou, *D1, origem='PAINEL')
        nunca = criar_cliente(nome='Nunca')
        so_marketing = criar_cliente(nome='Mkt', consent_email_marketing=True)
        for i, cli in enumerate((autorizou, revogou, nunca, so_marketing)):
            criar_atendimento(cli, self.prof, self.proc, data_hora=_as_14h_local() + timedelta(hours=i))
        fake = self._enviar()
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(fake.call_args.args[0], autorizou.telefone)

    def test_descadastro_nao_corta_o_lembrete_transacional(self):
        cli = criar_cliente(consent_whatsapp_confirmacao=True, consent_email_marketing=True)
        LgpdService.unsubscribe_por_token(criar_link('DESCADASTRO', cli))
        criar_atendimento(cli, self.prof, self.proc, data_hora=_as_14h_local())
        self.assertEqual(self._enviar().call_count, 1)


class NpsConsentimentoTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)

    def _realizado(self, cli):
        inicio = timezone.now() - timedelta(days=2)
        return criar_atendimento(cli, self.prof, self.proc, data_hora=inicio, status='REALIZADO')

    def test_candidatos_respeitam_ultimo_registro(self):
        from aranha_estetica.services.nps import candidatos
        ok = self._realizado(criar_cliente(consent_whatsapp_nps=True))
        descadastrada = criar_cliente(consent_whatsapp_nps=True)
        LgpdService.unsubscribe_por_token(criar_link('DESCADASTRO', descadastrada))
        self._realizado(descadastrada)
        voltou = criar_cliente(consent_whatsapp_nps=True)
        consentimentos.revogar(voltou, *NPS, origem='DESCADASTRO')
        consentimentos.registrar(voltou, *NPS, True, origem='AGENDAMENTO_ONLINE')
        at_voltou = self._realizado(voltou)
        self._realizado(criar_cliente(consent_whatsapp_confirmacao=True))  # outro par nao vale
        self.assertEqual(set(candidatos().values_list('pk', flat=True)), {ok.pk, at_voltou.pk})

    def test_contagem_de_falhas_continua_com_a_anotacao(self):
        from aranha_estetica.services.nps import candidatos
        at = self._realizado(criar_cliente(consent_whatsapp_nps=True))
        for _ in range(2):
            Notificacao.objects.create(atendimento=at, tipo='NPS', status='FALHOU')
        self.assertEqual(list(candidatos().values_list('pk', 'nps_falhas')), [(at.pk, 2)])


@override_settings(EMAIL_BACKEND=LOCMEM, SITE_URL='https://clinica.example.com')
class MarketingEmailTests(TestCase):
    def _clientes(self, **extra):
        autorizou = criar_cliente(nome='Autorizou', email='sim@example.com', consent_email_marketing=True, **extra)
        revogou = criar_cliente(nome='Revogou', email='nao@example.com', consent_email_marketing=True, **extra)
        LgpdService.unsubscribe_por_token(criar_link('DESCADASTRO', revogou))
        criar_cliente(nome='Nunca', email='nunca@example.com', **extra)
        criar_cliente(nome='So WhatsApp', email='wa@example.com', consent_whatsapp_nps=True, **extra)
        return autorizou

    def test_aniversario_so_para_marketing_vigente(self):
        from aranha_estetica.jobs import job_aniversario
        hoje = timezone.localdate()
        # 1992 e bissexto: hoje.replace() nao estoura em 29/02
        self._clientes(data_nascimento=hoje.replace(year=1992))
        job_aniversario()
        self.assertEqual([m.to[0] for m in mail.outbox], ['sim@example.com'])

    def test_promocao_so_para_marketing_vigente(self):
        from aranha_estetica.models import Promocao
        from aranha_estetica.services import marketing
        autorizou = self._clientes()
        hoje = timezone.localdate()
        promo = Promocao.objects.create(nome='Promo', descricao='<p>Oferta</p>', desconto_percentual=10,
                                        data_inicio=hoje, data_fim=hoje)
        self.assertEqual(list(marketing.destinatarios_promocao()), [autorizou])
        self.assertEqual(marketing.enviar_promocao(promo), (1, 0, 0))
        self.assertEqual([m.to[0] for m in mail.outbox], ['sim@example.com'])


class ListaEsperaWhatsAppTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)

    @patch('aranha_estetica.utils.whatsapp.enviar_template', return_value=True)
    def test_aviso_de_vaga_por_whatsapp_exige_lembrete_vigente(self, fake):
        from aranha_estetica.services.lista_espera import _enviar_wa_lista_espera
        slot = criar_atendimento(criar_cliente(), self.prof, self.proc)
        com = criar_cliente(consent_whatsapp_confirmacao=True)
        sem = criar_cliente(consent_whatsapp_confirmacao=True)
        consentimentos.revogar(sem, *D1, origem='PAINEL')

        def espera(cli):
            return ListaEspera.objects.create(cliente=cli, procedimento=self.proc,
                                              data_desejada=timezone.localdate())

        self.assertTrue(_enviar_wa_lista_espera(espera(com), slot, 'https://x/agendar'))
        self.assertFalse(_enviar_wa_lista_espera(espera(sem), slot, 'https://x/agendar'))
        self.assertEqual(fake.call_count, 1)


class PrefillWizardTests(TestCase):
    def test_estado_wizard_usa_as_chaves_dos_checkboxes(self):
        cli = criar_cliente(consent_whatsapp_confirmacao=True, consent_email_marketing=True)
        consentimentos.revogar(cli, *MKT, origem='DESCADASTRO')
        self.assertEqual(consentimentos.estado_wizard(cli), {
            'consent_email_marketing': False,
            'consent_whatsapp_nps': False,
            'consent_whatsapp_confirmacao': True,
        })

    def test_factory_conceder_registra_no_historico(self):
        cli = criar_cliente()
        conceder(cli, 'consent_whatsapp_nps', registrado_em=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))
        self.assertTrue(consentimentos.vigente(cli, *NPS))
