"""LGPD: direito ao esquecimento (anonimizacao persistida) e purga por retencao."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import (
    Cliente, CodigoOtp, FormularioAnamnese, ListaEspera, LogAuditoria, Notificacao,
    Prontuario, RespostaAnamnese,
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


def _ficha(cliente, atendimento=None, respondida=True, dias_atras=0):
    form, _ = FormularioAnamnese.objects.get_or_create(nome='Ficha de bem-estar')
    ficha = RespostaAnamnese.objects.create(
        formulario=form, cliente=cliente, atendimento=atendimento,
        respostas_json={'gestante': 'talvez'},
        respondida_em=timezone.now() if respondida else None,
    )
    if dias_atras:
        RespostaAnamnese.objects.filter(pk=ficha.pk).update(
            criado_em=timezone.now() - timedelta(days=dias_atras),
        )
    return ficha


class FichasSemAtendimentoTests(TestCase):
    """gap2-08: ficha de saude de pedido que nunca aconteceu nao fica para sempre."""

    def setUp(self):
        self.cliente = criar_cliente(nome='Dora Teste')
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)

    def _atd(self, status, dias=-100):
        quando = timezone.now() + timedelta(days=dias)
        return criar_atendimento(self.cliente, self.prof, self.proc, data_hora=quando, status=status)

    def test_ficha_de_cancelado_antiga_e_apagada(self):
        ficha = _ficha(self.cliente, self._atd('CANCELADO'), dias_atras=91)
        self.assertEqual(LgpdService.purgar_fichas_sem_atendimento(), 1)
        self.assertFalse(RespostaAnamnese.objects.filter(pk=ficha.pk).exists())
        self.assertTrue(LogAuditoria.objects.filter(tabela='resposta_anamnese').exists())

    def test_ficha_de_realizado_permanece(self):
        ficha = _ficha(self.cliente, self._atd('REALIZADO'), dias_atras=400)
        self.assertEqual(LgpdService.purgar_fichas_sem_atendimento(), 0)
        self.assertTrue(RespostaAnamnese.objects.filter(pk=ficha.pk).exists())

    def test_cliente_recorrente_nao_protege_ficha_de_pedido_cancelado(self):
        _ficha(self.cliente, self._atd('REALIZADO', dias=-200), dias_atras=200)
        cancelada = _ficha(self.cliente, self._atd('CANCELADO'), dias_atras=91)
        LgpdService.purgar_fichas_sem_atendimento()
        self.assertFalse(RespostaAnamnese.objects.filter(pk=cancelada.pk).exists())
        self.assertEqual(RespostaAnamnese.objects.filter(cliente=self.cliente).count(), 1)

    def test_pendente_vencido_e_convite_nunca_respondido_saem(self):
        _ficha(self.cliente, self._atd('PENDENTE'), dias_atras=91)
        _ficha(self.cliente, None, respondida=False, dias_atras=91)
        self.assertEqual(LgpdService.purgar_fichas_sem_atendimento(), 2)

    def test_ficha_recente_de_cancelado_ainda_fica(self):
        _ficha(self.cliente, self._atd('CANCELADO', dias=-10), dias_atras=10)
        self.assertEqual(LgpdService.purgar_fichas_sem_atendimento(), 0)

    def test_job_de_retencao_chama_a_purga_de_fichas(self):
        from aranha_estetica.tasks import job_lgpd_purgar_inativos
        _ficha(self.cliente, self._atd('CANCELADO'), dias_atras=91)
        resultado = job_lgpd_purgar_inativos.apply().result
        self.assertIn('1 fichas apagadas', resultado)
        self.assertFalse(RespostaAnamnese.objects.exists())

    def test_esquecer_apaga_ficha_de_cancelado_e_mantem_a_de_realizado(self):
        realizada = _ficha(self.cliente, self._atd('REALIZADO'))
        _ficha(self.cliente, self._atd('CANCELADO', dias=-50))
        _ficha(self.cliente, None)
        LgpdService.esquecer_cliente(self.cliente)
        self.assertEqual(
            list(RespostaAnamnese.objects.filter(cliente_id=self.cliente.pk).values_list('pk', flat=True)),
            [realizada.pk],
        )


class TrilhaAuditoriaAnonimizadaTests(TestCase):
    """gap2-06: depois do esquecimento o log nao liga mais nome, prontuario e id."""

    def test_nome_do_titular_sai_do_texto_do_log(self):
        from aranha_estetica.utils.audit import registrar_log
        cliente = criar_cliente(nome='Fabi Reis')
        outra = criar_cliente(nome='Carla Lima')
        pront = Prontuario.objects.create(cliente=cliente, alergias='nenhuma')
        registrar_log(None, 'Acessou prontuario de Fabi Reis', 'prontuario', cliente.pk)
        registrar_log(None, 'Atualizou prontuario de Fabi Reis', 'prontuario', pront.pk)
        registrar_log(None, 'Editou cliente: Fabi Reis', 'cliente', cliente.pk)
        registrar_log(None, 'Editou cliente: Carla Lima', 'cliente', outra.pk)

        LgpdService.esquecer_cliente(cliente)

        self.assertFalse(LogAuditoria.objects.filter(acao__contains='Fabi Reis').exists())
        self.assertEqual(
            LogAuditoria.objects.filter(acao__contains=f'[ANONIMIZADO-{cliente.pk}]').count(), 3,
        )
        # trilha preservada (tabela/id) e log de terceiros intacto
        self.assertTrue(LogAuditoria.objects.filter(tabela='prontuario', registro_id=pront.pk).exists())
        self.assertTrue(LogAuditoria.objects.filter(acao='Editou cliente: Carla Lima').exists())
