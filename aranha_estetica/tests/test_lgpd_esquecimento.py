"""LGPD: direito ao esquecimento (anonimizacao persistida) e purga por retencao."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import (
    Cliente, CodigoOtp, ListaEspera, LogAuditoria, Notificacao, Prontuario,
)
from aranha_estetica.services.lgpd import LgpdService

from .factories import (
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional,
)


class EsquecerClienteTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente(
            nome='Fulana Real', telefone='17900090008', email='fulana@example.com',
            cpf='52998224725', consent_email_marketing=True,
            consent_email_marketing_ip='10.0.0.1',
        )

    def test_anonimizacao_e_gravada_no_banco(self):
        """Regressao critica: soft_delete() gravava so deletado_em/ativo."""
        token_antigo = self.cliente.token_descadastro
        LgpdService.esquecer_cliente(self.cliente)

        salvo = Cliente.all_objects.get(pk=self.cliente.pk)
        self.assertTrue(salvo.nome.startswith('[ANONIMIZADO-'))
        self.assertIsNone(salvo.cpf)
        self.assertIsNone(salvo.email)
        self.assertIsNone(salvo.telefone)
        self.assertFalse(salvo.consent_email_marketing)
        self.assertIsNone(salvo.consent_email_marketing_ip)
        self.assertFalse(salvo.ativo)
        self.assertIsNotNone(salvo.deletado_em)
        self.assertNotEqual(salvo.token_descadastro, token_antigo)
        self.assertTrue(LogAuditoria.objects.filter(
            tabela='cliente', registro_id=self.cliente.pk, acao__icontains='anonimizado',
        ).exists())

    def test_apaga_rastros_de_pii_fora_do_cadastro(self):
        CodigoOtp.gerar_sms('17900090008', proposito=CodigoOtp.PROPOSITO_DSAR)
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        atd = criar_atendimento(self.cliente, prof, proc)
        Notificacao.objects.create(atendimento=atd, tipo='LEMBRETE', mensagem='Oi Fulana Real')
        ListaEspera.objects.create(
            cliente=self.cliente, procedimento=proc, data_desejada=timezone.localdate(),
        )

        LgpdService.esquecer_cliente(self.cliente)

        self.assertFalse(CodigoOtp.objects.filter(telefone='17900090008').exists())
        self.assertFalse(ListaEspera.objects.filter(cliente_id=self.cliente.pk).exists())
        self.assertEqual(Notificacao.objects.get(atendimento=atd).mensagem, '')

    def test_mesmo_telefone_pode_recadastrar(self):
        LgpdService.esquecer_cliente(self.cliente)
        novo = criar_cliente(nome='Outra', telefone='17900090008')
        self.assertTrue(novo.pk)


class PurgaRetencaoTests(TestCase):
    def _envelhecer(self, cliente, dias):
        Cliente.all_objects.filter(pk=cliente.pk).update(
            criado_em=timezone.now() - timedelta(days=dias),
        )

    def test_prazo_documentado_de_5_anos(self):
        self.assertEqual(LgpdService.RETENCAO_CLIENTE_INATIVO_DIAS, 365 * 5)

    def test_lead_inativo_ha_mais_de_5_anos_e_anonimizado(self):
        lead = criar_cliente(nome='Lead Antigo')
        self._envelhecer(lead, 365 * 5 + 10)
        self.assertEqual(LgpdService.purgar_inativos(), 1)
        self.assertTrue(Cliente.all_objects.get(pk=lead.pk).nome.startswith('[ANONIMIZADO-'))

    def test_inativo_ha_3_anos_nao_e_anonimizado(self):
        cliente = criar_cliente(nome='Recente')
        self._envelhecer(cliente, 365 * 3)
        self.assertEqual(LgpdService.purgar_inativos(), 0)
        self.assertEqual(Cliente.all_objects.get(pk=cliente.pk).nome, 'Recente')

    def test_cliente_com_prontuario_fica_retido(self):
        cliente = criar_cliente(nome='Com Prontuario')
        Prontuario.objects.create(cliente=cliente, alergias='nenhuma')
        self._envelhecer(cliente, 365 * 6)
        self.assertEqual(LgpdService.purgar_inativos(), 0)
        self.assertEqual(Cliente.all_objects.get(pk=cliente.pk).nome, 'Com Prontuario')

    def test_soft_deletado_ha_30_dias_e_anonimizado(self):
        cliente = criar_cliente(nome='Excluida', email='excluida@example.com')
        cliente.soft_delete()
        Cliente.all_objects.filter(pk=cliente.pk).update(
            deletado_em=timezone.now() - timedelta(days=31),
        )
        self.assertEqual(LgpdService.purgar_inativos(), 1)
        salvo = Cliente.all_objects.get(pk=cliente.pk)
        self.assertIsNone(salvo.email)
        # Ja anonimizado nao volta a ser processado
        self.assertEqual(LgpdService.purgar_inativos(), 0)
