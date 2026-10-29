"""Tests Sprint 1-2 + 3-4: F-RET, F-CSB, Comissao, Lista Espera."""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from aranha_estetica.models import (
    Atendimento,
    Carteira,
    ListaEspera,
    MovimentoComissao,
    MovimentoCarteira,
    RegraComissao,
)
from aranha_estetica.services.comissao import ComissaoService
from aranha_estetica.services.fidelidade import FidelidadeService
from aranha_estetica.excecoes import RegraDeNegocio
from aranha_estetica.services import retorno
from aranha_estetica.utils.datas import data_local, ler_hora

from .factories import (
    ja_comecou,
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional,
)


# ─── F-RET ────────────────────────────────────────────────────────────
class RetornoServiceTests(TestCase):
    """Retorno obrigatorio: fica DEVIDO no Realizado e a recepcao marca dentro da janela."""

    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.proc.exige_retorno = True
        self.proc.retorno_minimo_dias = 15
        self.proc.retorno_maximo_dias = 21
        self.proc.duracao_retorno_minutos = 30
        self.proc.save()
        self.cliente = criar_cliente()

    def _realizado(self, proc=None):
        atend = criar_atendimento(self.cliente, self.prof, proc or self.proc, status='AGENDADO')
        ja_comecou(atend).marcar_realizado()
        atend.refresh_from_db()
        return atend

    def _inicio_na_janela(self, atend, dias=17):
        """1o horario livre a partir de ``dias`` apos o atendimento (pula dia sem expediente)."""
        de, ate = retorno.janela(atend)
        dia = data_local(atend.data_hora_fim) + timedelta(days=dias)
        self.assertTrue(de <= dia <= ate)
        while not retorno.horarios(atend, dia):
            dia += timedelta(days=1)
            self.assertLessEqual(dia, ate, 'janela de retorno sem nenhum horario livre')
        hhmm = retorno.horarios(atend, dia)[0]
        return timezone.make_aware(datetime.combine(dia, ler_hora(hhmm)))

    def test_realizado_deixa_o_retorno_devido_sem_reservar(self):
        atend = self._realizado()
        self.assertFalse(Atendimento.objects.filter(atendimento_origem=atend).exists())
        (devido,) = retorno.devidos()
        self.assertEqual(devido.origem, atend)
        base = data_local(atend.data_hora_fim)
        self.assertEqual((devido.de, devido.ate), (base + timedelta(days=15), base + timedelta(days=21)))

    def test_agendar_cria_retorno_agendado_sem_custo_e_sai_dos_devidos(self):
        atend = self._realizado()
        novo = retorno.agendar(atend, self._inicio_na_janela(atend))
        self.assertEqual((novo.status, novo.valor_cobrado, novo.eh_retorno), ('AGENDADO', Decimal('0'), True))
        self.assertEqual(novo.atendimento_origem, atend)
        self.assertEqual(retorno.devidos(), [])
        with self.assertRaises(RegraDeNegocio) as ctx:
            retorno.agendar(atend, self._inicio_na_janela(atend, dias=18))
        self.assertEqual(ctx.exception.codigo, 'retorno_existente')

    def test_fora_da_janela_recusa(self):
        atend = self._realizado()
        dia = data_local(atend.data_hora_fim) + timedelta(days=30)
        self.assertEqual(retorno.horarios(atend, dia), [])
        with self.assertRaises(RegraDeNegocio) as ctx:
            retorno.agendar(atend, timezone.make_aware(datetime.combine(dia, ler_hora('10:00'))))
        self.assertEqual(ctx.exception.codigo, 'retorno_fora_da_janela')

    def test_sem_retorno_no_procedimento(self):
        atend = self._realizado(criar_procedimento(nome='Drenagem', profissional=self.prof))
        self.assertIsNone(retorno.janela(atend))
        self.assertEqual(retorno.devidos(), [])

    def test_janela_vencida_some_dos_devidos(self):
        atend = self._realizado()
        Atendimento.objects.filter(pk=atend.pk).update(
            data_hora_inicio=atend.data_hora_inicio - timedelta(days=40),
            data_hora_fim=atend.data_hora_fim - timedelta(days=40))
        self.assertEqual(retorno.devidos(), [])

    def test_retorno_gratuito_nao_consome_sessao_do_pacote(self):
        """Regressao gap3-05: o retorno (R$0) debitava uma sessao paga."""
        from aranha_estetica.models import ConsumoSessao
        from .factories import criar_compra_pacote, criar_pacote

        compra = criar_compra_pacote(self.cliente, criar_pacote(procedimento=self.proc, sessoes=3))
        atend = self._realizado()
        novo = retorno.agendar(atend, self._inicio_na_janela(atend))
        ja_comecou(novo).marcar_realizado()

        self.assertEqual(compra.sessoes_realizadas.count(), 1)
        self.assertFalse(ConsumoSessao.objects.filter(atendimento=novo).exists())
        compra.refresh_from_db()
        self.assertEqual(compra.status, 'ATIVO')


# ─── F-CSB ────────────────────────────────────────────────────────────
class FidelidadeServiceTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('500.00'))
        self.indicadora = criar_cliente(nome='Ana Indicadora')
        self.indicada = criar_cliente(
            nome='Bia Indicada', indicado_por=self.indicadora,
        )

    def _atend_realizado_pago(self, cliente, valor=Decimal('500.00')):
        atend = criar_atendimento(cliente, self.prof, self.proc, status='AGENDADO')
        atend.valor_cobrado = valor
        atend.save()
        ja_comecou(atend).marcar_realizado()
        atend.refresh_from_db()
        return atend

    def test_credita_indicador_no_primeiro_pago(self):
        self._atend_realizado_pago(self.indicada)
        cred = Carteira.objects.get(cliente=self.indicadora)
        self.assertEqual(cred.saldo, Decimal('50.00'))
        mov = MovimentoCarteira.objects.get(carteira=cred, origem='CASHBACK_INDICACAO')
        self.assertEqual(mov.valor, Decimal('50.00'))

    def test_nao_credita_se_segundo_pago(self):
        self._atend_realizado_pago(self.indicada)
        # 2o atendimento pago da indicada — nao deve gerar novo cashback
        self._atend_realizado_pago(self.indicada)
        cred = Carteira.objects.get(cliente=self.indicadora)
        self.assertEqual(cred.saldo, Decimal('50.00'))  # ainda 1 credito so

    def test_nao_credita_se_indicada_sem_indicador(self):
        cliente_sem_indicador = criar_cliente(nome='Sozinha')
        self._atend_realizado_pago(cliente_sem_indicador)
        self.assertFalse(Carteira.objects.filter(cliente=self.indicadora).exists())

    def test_nao_credita_se_valor_zero(self):
        atend = criar_atendimento(self.indicada, self.prof, self.proc, status='AGENDADO')
        atend.valor_cobrado = Decimal('0.00')
        atend.save()
        ja_comecou(atend).marcar_realizado()
        self.assertFalse(Carteira.objects.filter(cliente=self.indicadora).exists())

    def test_estorna_em_cancelamento(self):
        atend = self._atend_realizado_pago(self.indicada)
        # Forcar cancelamento (FSM nao permite REALIZADO->CANCELADO normal,
        # entao chamamos service direto p/ teste do estorno)
        FidelidadeService.estornar_cashback_de_atendimento(atend, motivo='teste')
        cred = Carteira.objects.get(cliente=self.indicadora)
        self.assertEqual(cred.saldo, Decimal('0.00'))
        estorno = MovimentoCarteira.objects.filter(
            carteira=cred, origem='CASHBACK_ESTORNO',
        ).first()
        self.assertIsNotNone(estorno)


# ─── Comissao ─────────────────────────────────────────────────────────
class ComissaoServiceTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('1000.00'))
        self.cliente = criar_cliente()
        self.regra = RegraComissao.objects.create(
            profissional=self.prof, procedimento=self.proc,
            percentual=Decimal('30.00'), ativo=True,
        )

    def test_calcula_comissao_percentual(self):
        atend = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')
        atend.valor_cobrado = Decimal('1000.00')
        atend.save()
        ja_comecou(atend).marcar_realizado()
        atend.refresh_from_db()
        mov = MovimentoComissao.objects.filter(atendimento=atend).first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.valor, Decimal('300.00'))

    def test_resolver_regra_mais_especifica(self):
        # Regra geral menos especifica
        RegraComissao.objects.create(
            profissional=None, procedimento=None,
            percentual=Decimal('10.00'), ativo=True,
        )
        regra = ComissaoService.resolver_regra(self.prof.pk, self.proc.pk)
        self.assertEqual(regra.percentual, Decimal('30.00'))

    def test_idempotente_nao_duplica(self):
        atend = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')
        atend.valor_cobrado = Decimal('1000.00')
        atend.save()
        ja_comecou(atend).marcar_realizado()
        # Tenta calcular de novo
        ComissaoService.calcular_comissao(atend)
        count = MovimentoComissao.objects.filter(atendimento=atend).count()
        self.assertEqual(count, 1)


# ─── Lista de Espera ──────────────────────────────────────────────────
ENVIAR_EMAIL = 'aranha_estetica.utils.email.enviar_fila_espera_email'
ENVIAR_WA = 'aranha_estetica.utils.whatsapp.enviar_template'


class ListaEsperaHandlerTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cliente_agendado = criar_cliente(nome='Agendado')
        self.cliente_espera = criar_cliente(
            nome='Espera', telefone='17999991111', email='espera@example.com',
            consent_whatsapp_confirmacao=True,
        )

    def _agendar(self, quando):
        return criar_atendimento(
            self.cliente_agendado, self.prof, self.proc, status='AGENDADO', data_hora=quando,
        )

    def _entrar_na_fila(self, atend):
        return ListaEspera.objects.create(
            cliente=self.cliente_espera,
            procedimento=self.proc,
            data_desejada=timezone.localdate(atend.data_hora_inicio),
        )

    @patch(ENVIAR_WA, return_value=True)
    @patch(ENVIAR_EMAIL, return_value=True)
    def test_notifica_compativeis_em_cancelamento(self, mock_email, mock_wa):
        atend = self._agendar(timezone.now() + timedelta(days=2))
        self._entrar_na_fila(atend)

        with self.captureOnCommitCallbacks(execute=True):
            atend.cancelar(motivo='teste')

        espera = ListaEspera.objects.get(cliente=self.cliente_espera)
        self.assertIsNotNone(espera.ultimo_aviso_em)   # avisada e continua na fila
        # Reserva por token nunca existiu de fato: colunas legadas removidas na 0049.
        self.assertFalse({'token_reserva', 'expira_em'} & {f.name for f in ListaEspera._meta.fields})
        mock_email.assert_called_once()
        self.assertIn(f'procedimento={self.proc.pk}', mock_email.call_args[0][1]['link'])
        mock_wa.assert_called_once()

    @patch(ENVIAR_WA, return_value=False)
    @patch(ENVIAR_EMAIL, return_value=True)
    def test_aviso_vai_para_o_email_informado_na_inscricao(self, mock_email, _mock_wa):
        atend = self._agendar(timezone.now() + timedelta(days=2))
        espera = self._entrar_na_fila(atend)
        espera.email_contato = 'novo.contato@example.com'
        espera.save()
        with self.captureOnCommitCallbacks(execute=True):
            atend.cancelar(motivo='teste')
        self.assertEqual(mock_email.call_args[0][0], 'novo.contato@example.com')

    @patch(ENVIAR_WA, return_value=True)
    @patch(ENVIAR_EMAIL, return_value=True)
    def test_envio_so_apos_commit(self, mock_email, mock_wa):
        """E-mail/WhatsApp nunca saem dentro da transacao do cancelamento."""
        atend = self._agendar(timezone.now() + timedelta(days=2))
        self._entrar_na_fila(atend)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            atend.cancelar(motivo='teste')
        mock_email.assert_not_called()
        mock_wa.assert_not_called()
        self.assertTrue(callbacks)

    @patch(ENVIAR_WA, return_value=True)
    @patch(ENVIAR_EMAIL, return_value=True)
    def test_vaga_no_passado_nao_avisa(self, mock_email, mock_wa):
        atend = self._agendar(timezone.now() - timedelta(days=2))
        self._entrar_na_fila(atend)
        with self.captureOnCommitCallbacks(execute=True):
            atend.cancelar(motivo='expirado')
        mock_email.assert_not_called()
        self.assertIsNone(ListaEspera.objects.get(cliente=self.cliente_espera).ultimo_aviso_em)

    @patch(ENVIAR_WA, return_value=False)
    @patch(ENVIAR_EMAIL, return_value=False)
    def test_sem_entrega_continua_na_fila(self, _mock_email, _mock_wa):
        """Canal nao configurado/falhou: aviso nao registrado (equipe avisa manualmente)."""
        atend = self._agendar(timezone.now() + timedelta(days=2))
        self._entrar_na_fila(atend)
        with self.captureOnCommitCallbacks(execute=True):
            atend.cancelar(motivo='teste')
        self.assertIsNone(ListaEspera.objects.get(cliente=self.cliente_espera).ultimo_aviso_em)

    @patch(ENVIAR_WA, return_value=True)
    @patch(ENVIAR_EMAIL, return_value=True)
    def test_respeita_profissional_desejado(self, mock_email, _mock_wa):
        outro_prof = criar_profissional(nome='Outra')
        atend = self._agendar(timezone.now() + timedelta(days=2))
        espera = self._entrar_na_fila(atend)
        espera.profissional_desejado = outro_prof
        espera.save()
        with self.captureOnCommitCallbacks(execute=True):
            atend.cancelar(motivo='teste')
        mock_email.assert_not_called()


class CreditoIndicacaoUsoTests(TestCase):
    """O credito de indicacao agora e visivel e usavel: abate no atendimento e volta se nao acontecer."""

    def setUp(self):
        from decimal import Decimal

        from aranha_estetica.models import Carteira, MovimentoCarteira
        from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente()
        carteira = Carteira.objects.create(cliente=self.cli, saldo=Decimal('50.00'))
        MovimentoCarteira.objects.create(carteira=carteira, tipo='CREDITO', origem='CASHBACK_INDICACAO',
                                         valor=Decimal('50.00'), saldo_resultante=Decimal('50.00'))
        self.at = criar_atendimento(self.cli, self.prof, self.proc)
        type(self.at).objects.filter(pk=self.at.pk).update(valor_cobrado=Decimal('30.00'))
        self.at.refresh_from_db()

    def test_abate_ate_o_valor_e_devolve_ao_cancelar(self):
        from decimal import Decimal

        from aranha_estetica.excecoes import RegraDeNegocio
        from aranha_estetica.models import Carteira
        from aranha_estetica.services.fidelidade import FidelidadeService
        with self.assertRaises(RegraDeNegocio):
            FidelidadeService.usar_credito(self.at, Decimal('40.00'))     # acima do valor do atendimento
        FidelidadeService.usar_credito(self.at, Decimal('30.00'))
        self.assertEqual(Carteira.objects.get(cliente=self.cli).saldo, Decimal('20.00'))
        self.assertEqual(FidelidadeService.creditos_usados([self.at.pk]), {self.at.pk: Decimal('30.00')})
        with self.assertRaises(RegraDeNegocio):
            FidelidadeService.usar_credito(self.at, Decimal('10.00'))     # 1 uso por atendimento
        self.at.cancelar(motivo='desistiu')
        self.assertEqual(Carteira.objects.get(cliente=self.cli).saldo, Decimal('50.00'))
        self.assertEqual(FidelidadeService.creditos_usados([self.at.pk]), {})

    def test_painel_abate_pelo_formulario_de_valor_e_ficha_mostra_extrato(self):
        from decimal import Decimal

        from django.urls import reverse

        from aranha_estetica.models import Carteira, Usuario
        admin = Usuario.objects.create_superuser(email='adm@credito.test', password='x')
        self.client.force_login(admin)
        self.client.post(reverse('aranha:admin_atendimento_valor', args=[self.at.pk]),
                         {'valor': '30,00', 'usar_credito': '1'})
        self.assertEqual(Carteira.objects.get(cliente=self.cli).saldo, Decimal('20.00'))
        ficha = self.client.get(reverse('aranha:admin_cliente_detalhe', args=[self.cli.pk]))
        self.assertContains(ficha, 'Uso em atendimento')
        self.assertContains(ficha, 'Crédito de indicação disponível')


class RetornoTelasTests(TestCase):
    """Painel (lista + marcar retorno), card da visao geral e aviso em Meus agendamentos."""

    def setUp(self):
        from django.core.cache import cache

        from aranha_estetica.models import Usuario
        cache.clear()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.proc.exige_retorno, self.proc.retorno_minimo_dias, self.proc.retorno_maximo_dias = True, 7, 14
        self.proc.save()
        self.cliente = criar_cliente()
        self.origem = criar_atendimento(self.cliente, self.prof, self.proc)
        ja_comecou(self.origem).marcar_realizado()
        admin = Usuario.objects.create_user(email='admin-ret@test.com', password='senha-forte-123',
                                            papel=Usuario.PAPEL_ADMIN)
        self.client.force_login(admin)

    def test_lista_visao_geral_e_ficha_mostram_o_retorno(self):
        from django.urls import reverse
        url = reverse('aranha:admin_agendar_retorno', args=[self.origem.pk])
        self.assertContains(self.client.get(reverse('aranha:painel_retornos')), url)
        self.assertContains(self.client.get(reverse('aranha:painel_overview')), '1 retorno a marcar')
        self.assertContains(self.client.get(reverse('aranha:admin_cliente_detalhe', args=[self.cliente.pk])), url)

    def test_marcar_pela_tela(self):
        from django.urls import reverse
        de, _ate = retorno.janela(self.origem)
        url = reverse('aranha:admin_agendar_retorno', args=[self.origem.pk])
        tela = self.client.get(url, {'data': de.isoformat()})
        hora = tela.context['slots'][0]
        resp = self.client.post(url, {'data': de.isoformat(), 'hora': hora, 'profissional_id': self.prof.pk})
        self.assertEqual(resp.status_code, 302)
        novo = Atendimento.objects.get(atendimento_origem=self.origem)
        self.assertEqual((novo.status, novo.eh_retorno), ('AGENDADO', True))

    def test_portal_avisa_a_cliente(self):
        from django.urls import reverse

        from aranha_estetica.services.portal_cliente import SESSAO_MEUS_AGENDAMENTOS
        cliente = Client()
        sessao = cliente.session
        sessao[SESSAO_MEUS_AGENDAMENTOS] = self.cliente.telefone
        sessao.save()
        resp = cliente.get(reverse('aranha:meus_agendamentos'))
        self.assertContains(resp, f'Seu retorno de {self.proc.nome} é sem custo.')
