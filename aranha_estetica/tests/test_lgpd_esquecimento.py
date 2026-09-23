"""LGPD: direito ao esquecimento (anonimizacao persistida) e purga por retencao."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import (
    AceiteTermo, AvaliacaoNPS, Cliente, CodigoOtp, FormularioAnamnese, ListaEspera,
    LogAuditoria, Notificacao, Prontuario, RespostaAnamnese, VersaoTermo,
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

    def _aceite_lgpd(self, cliente):
        termo = VersaoTermo.lgpd_vigente() or VersaoTermo.objects.create(
            tipo='LGPD', titulo='Politica de privacidade', versao='teste', conteudo='Texto', ativa=True,
            vigente_desde=timezone.localdate(),
        )
        return AceiteTermo.registrar(cliente, termo)

    def test_aceite_lgpd_nao_retem_cliente_inativo(self):
        """rev_security-03: todo booking grava aceite — a purga nao pode virar no-op."""
        cliente = criar_cliente(nome='Velha Com Aceite')
        aceite = self._aceite_lgpd(cliente)
        self._envelhecer(cliente, 365 * 6)
        self.assertEqual(LgpdService.purgar_inativos(), 1)
        self.assertTrue(Cliente.all_objects.get(pk=cliente.pk).nome.startswith('[ANONIMIZADO-'))
        # prova do consentimento continua ligada ao mesmo id
        self.assertEqual(AceiteTermo.objects.get(pk=aceite.pk).cliente_id, cliente.pk)

    def test_soft_deletado_com_aceite_e_anonimizado(self):
        cliente = criar_cliente(nome='Pediu Exclusao')
        self._aceite_lgpd(cliente)
        cliente.soft_delete()
        Cliente.all_objects.filter(pk=cliente.pk).update(
            deletado_em=timezone.now() - timedelta(days=31),
        )
        self.assertIn(cliente.pk, set(LgpdService.candidatos_purga().values_list('pk', flat=True)))

    def test_prontuario_vazio_nao_retem(self):
        """followups-purga-prontuario-vazio: registro vazio (GET legado) nao conta."""
        vazio = criar_cliente(nome='Prontuario Vazio')
        Prontuario.objects.create(cliente=vazio)
        branco = criar_cliente(nome='Prontuario Branco')
        Prontuario.objects.create(cliente=branco, alergias='', respostas_extras={})
        for cliente in (vazio, branco):
            self._envelhecer(cliente, 365 * 6)
        candidatos = set(LgpdService.candidatos_purga().values_list('pk', flat=True))
        self.assertEqual(candidatos, {vazio.pk, branco.pk})

    def test_prontuario_so_com_respostas_extras_retem(self):
        cliente = criar_cliente(nome='Com Extras')
        Prontuario.objects.create(cliente=cliente, respostas_extras={'diabetes': True})
        self._envelhecer(cliente, 365 * 6)
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

    def test_convite_vazio_de_horario_passado_sai_e_ficha_legada_fica(self):
        """rev_painel-11: link da recepcao ignorado nao fica para sempre; ficha do
        booking antigo (respostas + respondida_em nulo) nunca entra na purga."""
        realizado = self._atd('REALIZADO')
        convite = _ficha(self.cliente, realizado, respondida=False, dias_atras=91)
        RespostaAnamnese.objects.filter(pk=convite.pk).update(respostas_json={})
        legada = _ficha(self.cliente, self._atd('REALIZADO', dias=-120), respondida=False, dias_atras=120)
        futuro = _ficha(self.cliente, self._atd('AGENDADO', dias=5), respondida=False, dias_atras=91)
        RespostaAnamnese.objects.filter(pk=futuro.pk).update(respostas_json={})

        self.assertEqual(LgpdService.purgar_fichas_sem_atendimento(), 1)

        self.assertFalse(RespostaAnamnese.objects.filter(pk=convite.pk).exists())
        self.assertTrue(RespostaAnamnese.objects.filter(pk=legada.pk).exists())
        self.assertTrue(RespostaAnamnese.objects.filter(pk=futuro.pk).exists())

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

    def test_log_de_pacote_e_nome_antigo_saem(self):
        """rev_security-07: 'Vendeu pacote ... para <nome>' e nome anterior a edicao."""
        from aranha_estetica.utils.audit import registrar_log

        from .factories import criar_compra_pacote, criar_pacote
        cliente = criar_cliente(nome='Fernanda Rastreavel')
        compra = criar_compra_pacote(cliente, criar_pacote())
        registrar_log(None, 'Vendeu pacote "Pacote Glow" para Fernanda Rastreavel', 'compra_pacote', compra.pk)
        registrar_log(None, 'Cancelou pacote "Pacote Glow" de Fernanda Rastreavel', 'compra_pacote', compra.pk)
        registrar_log(None, 'Editou cliente: Fernanda Antigo Sobrenome', 'cliente', cliente.pk)

        LgpdService.esquecer_cliente(cliente)

        self.assertFalse(LogAuditoria.objects.filter(acao__contains='Fernanda').exists())
        self.assertEqual(
            LogAuditoria.objects.filter(tabela='compra_pacote', registro_id=compra.pk).count(), 2,
        )

    def test_django_admin_log_perde_o_nome(self):
        from django.contrib.admin.models import CHANGE, LogEntry
        from django.contrib.contenttypes.models import ContentType

        from aranha_estetica.models import Usuario
        admin = Usuario.objects.create_user(email='adm-log@test.com', password='x-senha-123', nome='Adm')
        cliente = criar_cliente(nome='Nome Antigo Admin')
        prof = criar_profissional()
        atd = criar_atendimento(cliente, prof, criar_procedimento(profissional=prof))
        for obj in (cliente, atd):
            LogEntry.objects.create(
                user=admin, content_type=ContentType.objects.get_for_model(obj),
                object_id=str(obj.pk), object_repr=str(obj), action_flag=CHANGE,
                change_message='[{"changed": {"fields": ["Nome"]}}]',
            )
        outra = criar_cliente(nome='Terceira Pessoa')
        LogEntry.objects.create(
            user=admin, content_type=ContentType.objects.get_for_model(outra),
            object_id=str(outra.pk), object_repr=str(outra), action_flag=CHANGE,
        )

        LgpdService.esquecer_cliente(cliente)

        self.assertFalse(LogEntry.objects.filter(object_repr__contains='Nome Antigo').exists())
        self.assertEqual(LogEntry.objects.filter(object_repr=f'[ANONIMIZADO-{cliente.pk}]').count(), 2)
        self.assertTrue(LogEntry.objects.filter(object_repr='Terceira Pessoa').exists())

    def test_detalhes_da_migration_0034_sao_limpos(self):
        """pgupgrade-07: telefone original guardado pela 0034 nao sobrevive ao esquecimento."""
        cliente = criar_cliente(nome='Dona Lurdes')
        LogAuditoria.objects.create(
            acao='migration 0034: telefone invalido removido', tabela='cliente', registro_id=cliente.pk,
            detalhes={'telefone': '99****9999', 'telefone_original': '999999999'},
        )
        outro = LogAuditoria.objects.create(
            acao='migration 0034: telefone invalido removido', tabela='cliente', registro_id=cliente.pk + 999,
            detalhes={'telefone': '11****1111'},
        )

        LgpdService.esquecer_cliente(cliente)

        self.assertIsNone(LogAuditoria.objects.get(
            tabela='cliente', registro_id=cliente.pk, acao__startswith='migration 0034',
        ).detalhes)
        outro.refresh_from_db()
        self.assertEqual(outro.detalhes, {'telefone': '11****1111'})


class DepoimentoAposEsquecimentoTests(TestCase):
    """rev_security-02: esquecimento tira o depoimento do site e apaga o texto."""

    def test_nps_perde_autorizacao_e_comentario(self):
        cliente = criar_cliente(nome='Rita Depoimento')
        prof = criar_profissional()
        atd = criar_atendimento(cliente, prof, criar_procedimento(profissional=prof), status='REALIZADO')
        nps = AvaliacaoNPS.objects.create(
            atendimento=atd, nota=10, comentario='Amei o atendimento da Rita',
            autoriza_publicacao=True, aprovado_publicacao=True,
        )

        LgpdService.esquecer_cliente(cliente)

        nps.refresh_from_db()
        self.assertEqual(nps.nota, 10)
        self.assertEqual(nps.comentario, '')
        self.assertFalse(nps.autoriza_publicacao)
        self.assertFalse(nps.aprovado_publicacao)
        resp = self.client.get(reverse('aranha:depoimentos'))
        self.assertNotContains(resp, 'Amei o atendimento')
        self.assertNotContains(resp, '[ANONIMIZADO-')


class ExportDsarTests(TestCase):
    def test_lista_espera_exporta_email_de_contato_e_profissional(self):
        """followups-dsar-email-contato."""
        cliente = criar_cliente(nome='Titular Espera')
        prof = criar_profissional(nome='Dra. Desejada')
        proc = criar_procedimento(profissional=prof)
        ListaEspera.objects.create(
            cliente=cliente, procedimento=proc, profissional_desejado=prof,
            data_desejada=timezone.localdate(), email_contato='contato@x.com',
        )
        item = LgpdService.exportar_dados_cliente(cliente)['lista_espera'][0]
        self.assertEqual(item['email_contato'], 'contato@x.com')
        self.assertEqual(item['profissional_desejado'], 'Dra. Desejada')

    def test_exporta_versoes_do_prontuario(self):
        """followups-prontuario-sem-historico: DSAR inclui o historico clinico."""
        from aranha_estetica.models import ProntuarioVersao
        cliente = criar_cliente(nome='Titular Prontuario')
        pront = Prontuario.objects.create(cliente=cliente, alergias='dipirona')
        ProntuarioVersao.registrar(pront, None)
        Prontuario.objects.filter(pk=pront.pk).update(alergias='nenhuma')

        dados = LgpdService.exportar_dados_cliente(cliente)

        self.assertEqual(len(dados['prontuario_versoes']), 1)
        self.assertEqual(dados['prontuario_versoes'][0]['dados']['alergias'], 'dipirona')
        self.assertEqual(dados['prontuario']['alergias'], 'nenhuma')


class ListaEsperaVencidaTests(TestCase):
    """rev_security-01 (5): inscricao com data passada e apagada pela retencao."""

    def test_purga_so_data_passada_e_job_chama(self):
        from aranha_estetica.tasks import job_lgpd_purgar_inativos
        cliente = criar_cliente(nome='Espera Antiga')
        proc = criar_procedimento()
        hoje = timezone.localdate()
        vencida = ListaEspera.objects.create(
            cliente=cliente, procedimento=proc, data_desejada=hoje - timedelta(days=1),
            email_contato='x@example.com',
        )
        de_hoje = ListaEspera.objects.create(cliente=cliente, procedimento=proc, data_desejada=hoje)

        resultado = job_lgpd_purgar_inativos.apply().result

        self.assertIn('1 inscricoes de espera apagadas', resultado)
        self.assertFalse(ListaEspera.objects.filter(pk=vencida.pk).exists())
        self.assertTrue(ListaEspera.objects.filter(pk=de_hoje.pk).exists())
