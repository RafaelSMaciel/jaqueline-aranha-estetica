"""Caso de uso do pedido pelo site (services/agendamento_online) — sem cliente HTTP.

A suite HTTP (test_confirmar_agendamento, test_booking_consentimento_preco, ...) cobre a porta;
aqui a regra e chamada direto, como faria outra porta (painel, API): um teste por recusa.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.utils import timezone

from aranha_estetica.constants import MAX_ATIVOS_ONLINE
from aranha_estetica.excecoes import HorarioIndisponivel, RegraDeNegocio
from aranha_estetica.models import (
    AceiteTermo,
    Atendimento,
    Cliente,
    FormularioAnamnese,
    LogAuditoria,
    Notificacao,
    Promocao,
    RespostaAnamnese,
    VersaoTermo,
)
from aranha_estetica.services import agendamento_online
from aranha_estetica.services.agendamento_online import PedidoAgendamento

from ..factories import (
    criar_atendimento, criar_cliente, criar_compra_pacote, criar_pacote, criar_procedimento,
    criar_profissional, limpar_feriados,
)


def _inicio(dias=3, hora=10):
    dia = timezone.localdate() + timedelta(days=dias)
    return timezone.localtime(timezone.make_aware(datetime.combine(dia, time(hora, 0))))


def _emails(at, tipo):
    return Notificacao.objects.filter(atendimento=at, tipo=tipo, canal=Notificacao.Canal.EMAIL).count()


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        limpar_feriados()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('150.00'))
        self.lgpd = VersaoTermo.lgpd_vigente() or VersaoTermo.objects.create(
            tipo='LGPD', titulo='Política', conteudo='Resumo.', versao='1.0',
            vigente_desde=timezone.localdate(),
        )
        self.request = RequestFactory().post('/agendamento/confirmar/', REMOTE_ADDR='203.0.113.9',
                                             HTTP_USER_AGENT='Teste/1.0')

    def _pedido(self, **campos) -> PedidoAgendamento:
        dados = {
            'nome': 'Dora Teste', 'telefone': '17988887777', 'data_nascimento': timezone.localdate().replace(year=1990),
            'email': None, 'procedimento': self.proc, 'profissional': self.prof, 'inicio': _inicio(),
        }
        dados.update(campos)
        return PedidoAgendamento(**dados)

    def _solicitar(self, **campos) -> Atendimento:
        return agendamento_online.solicitar(self._pedido(**campos), request=self.request)

    def _recusa(self, codigo, **campos):
        antes = Atendimento.objects.count()
        with self.assertRaises(RegraDeNegocio) as ctx:
            self._solicitar(**campos)
        self.assertEqual(ctx.exception.codigo, codigo)
        self.assertEqual(Atendimento.objects.count(), antes)
        return ctx.exception

    def _ficha(self, obrigatorio=True):
        return FormularioAnamnese.objects.create(
            nome='Ficha', tipo='ANAMNESE', escopo='PROCEDIMENTO', procedimento=self.proc, obrigatorio=obrigatorio,
            schema_json=[{'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante?', 'obrigatorio': True}],
        )


class CaminhoFelizTests(_Base):
    def test_cliente_nova_vira_pedido_com_prova_do_aceite(self):
        with patch('aranha_estetica.services.comunicacao.enfileirar_email') as enfileirar:
            at = self._solicitar()
        self.assertEqual(at.status, Atendimento.STATUS_PENDENTE)
        self.assertEqual(at.valor_cobrado, Decimal('150.00'))
        self.assertEqual(at.cliente.nome, 'Dora Teste')
        # pedido p/ a profissional aprovar; a cliente so recebe a confirmacao depois
        enfileirar.assert_called_once_with(at, Notificacao.Tipo.APROVACAO)
        aceite = AceiteTermo.objects.get(versao_termo=self.lgpd)
        self.assertEqual((aceite.atendimento, str(aceite.ip), aceite.user_agent), (at, '203.0.113.9', 'Teste/1.0'))

    def test_cliente_de_confianca_nasce_agendada_com_confirmacao(self):
        cli = criar_cliente(telefone='17988887777', email='dora@exemplo.test')
        criar_atendimento(cli, self.prof, self.proc, status='REALIZADO', data_hora=timezone.now() - timedelta(days=9))
        at = self._solicitar()
        self.assertEqual(at.cliente, cli)
        self.assertEqual(at.status, Atendimento.STATUS_AGENDADO)
        self.assertEqual(_emails(at, Notificacao.Tipo.CONFIRMACAO), 1)
        self.assertEqual(_emails(at, Notificacao.Tipo.APROVACAO), 0)

    def test_nome_divergente_nao_altera_o_cadastro_da_titular(self):
        """D16: quem agenda p/ outra pessoa com o proprio celular nao renomeia a titular."""
        cli = criar_cliente(nome='Titular Original', telefone='17988887777')
        at = self._solicitar(nome='Outra Pessoa', email='nova@exemplo.test')
        cli.refresh_from_db()
        self.assertEqual(at.cliente, cli)
        self.assertEqual(cli.nome, 'Titular Original')
        self.assertEqual(cli.email, 'nova@exemplo.test')   # e-mail da dona do celular continua valendo

    def test_ficha_com_consentimento_grava_resposta_aceite_saude_e_trilha(self):
        ficha = self._ficha()
        at = self._solicitar(anamneses={str(ficha.pk): {'gestante': 'nao'}}, consent_saude=True)
        resposta = RespostaAnamnese.objects.get()
        self.assertEqual((resposta.atendimento, resposta.respostas_json), (at, {'gestante': False}))
        self.assertTrue(LogAuditoria.objects.filter(acao__contains='art. 11', registro_id=at.pk).exists())

    def test_termo_marcado_e_registrado_com_o_atendimento(self):
        termo = VersaoTermo.objects.create(tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo Toxina',
                                           conteudo='Riscos.', versao='1', vigente_desde=timezone.localdate())
        at = self._solicitar(termos_marcados=frozenset({termo.pk}))
        self.assertTrue(AceiteTermo.objects.filter(versao_termo=termo, atendimento=at).exists())


class RecusasTests(_Base):
    def test_sem_termo_lgpd(self):
        VersaoTermo.objects.filter(tipo='LGPD').update(ativa=False)
        with self.assertLogs('aranha_estetica.services.agendamento_online', 'ERROR'):
            self._recusa('sem_termo_lgpd')
        self.assertFalse(Cliente.objects.filter(telefone='17988887777').exists())

    def test_horario_fora_da_agenda(self):
        self._recusa('horario_indisponivel', inicio=_inicio(hora=3))

    def test_ficha_obrigatoria_sem_resposta(self):
        self._ficha()
        self._recusa('ficha_invalida')

    def test_ficha_sem_consentimento_de_saude(self):
        ficha = self._ficha()
        self._recusa('consent_saude_ausente', anamneses={str(ficha.pk): {'gestante': 'nao'}})
        self.assertEqual(RespostaAnamnese.objects.count(), 0)

    def test_termo_do_procedimento_pendente(self):
        VersaoTermo.objects.create(tipo='PROCEDIMENTO', procedimento=None, titulo='Termo geral',
                                   conteudo='Riscos.', versao='1', vigente_desde=timezone.localdate())
        recusa = self._recusa('termo_pendente')
        self.assertIn('Termo geral', recusa.mensagem)

    def test_termo_ja_aceito_pela_titular_nao_e_exigido_de_novo(self):
        termo = VersaoTermo.objects.create(tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo',
                                           conteudo='Riscos.', versao='1', vigente_desde=timezone.localdate())
        AceiteTermo.registrar(criar_cliente(telefone='17988887777'), termo)
        self.assertIsNotNone(self._solicitar())

    def test_email_de_outro_cadastro(self):
        criar_cliente(telefone='17911112222', email='dona@exemplo.test')
        self._recusa('email_de_outro_cadastro', email='dona@exemplo.test')

    def test_horario_em_confirmacao_por_outra_pessoa(self):
        pedido = self._pedido()
        cache.add(f'booking_slot:{self.prof.pk}:{pedido.inicio.isoformat()}', '1', timeout=30)
        self._recusa('horario_em_confirmacao')

    def test_cadastro_bloqueado_online(self):
        criar_cliente(telefone='17988887777', bloqueado_online=True)
        self._recusa('bloqueado_online')

    def test_teto_de_pedidos_em_aberto(self):
        """D8: MAX_ATIVOS_ONLINE horarios futuros ativos -> o proximo pedido vai p/ o WhatsApp; passado,
        cancelado ou realizado nao conta."""
        cli = criar_cliente(telefone='17988887777')
        for dias in range(5, 5 + MAX_ATIVOS_ONLINE - 1):
            criar_atendimento(cli, self.prof, self.proc, data_hora=_inicio(dias=dias))
        criar_atendimento(cli, self.prof, self.proc, status='CANCELADO', data_hora=_inicio(dias=9))
        criar_atendimento(cli, self.prof, self.proc, status='REALIZADO', data_hora=timezone.now() - timedelta(days=9))
        self.assertIsNotNone(self._solicitar())   # o 3o ainda passa
        recusa = self._recusa('limite_online', inicio=_inicio(dias=4))
        self.assertIn('WhatsApp', recusa.mensagem)

    def test_horario_ocupado_na_corrida(self):
        # outro pedido levou o horario entre a checagem do slot e o INSERT
        criar_atendimento(criar_cliente(), self.prof, self.proc, data_hora=_inicio())
        with patch('aranha_estetica.services.agenda.validar_horario'):
            recusa = self._recusa('horario_ocupado')
        self.assertIsInstance(recusa, HorarioIndisponivel)

    def test_recusa_dentro_da_transacao_libera_o_horario(self):
        criar_cliente(telefone='17988887777', bloqueado_online=True)
        self._recusa('bloqueado_online')
        self.assertTrue(cache.add(f'booking_slot:{self.prof.pk}:{_inicio().isoformat()}', '1'))


class ResumoConfirmacaoTests(_Base):
    def test_valor_e_status_do_pedido(self):
        resumo = agendamento_online.resumo_confirmacao(self._solicitar())
        self.assertEqual(resumo['valor'], 'R$ 150,00')
        self.assertEqual((resumo['valor_cheio'], resumo['promocao']), ('', ''))
        self.assertTrue(resumo['pendente'])
        self.assertFalse(resumo['reagendamento'])
        self.assertIn('às 10:00', resumo['data_hora'])

    def test_promocao_mostra_cheio_e_nome(self):
        Promocao.objects.create(nome='Semana Glow', procedimento=self.proc, desconto_percentual=Decimal('20'),
                                data_inicio=timezone.localdate(), data_fim=timezone.localdate() + timedelta(days=10))
        resumo = agendamento_online.resumo_confirmacao(self._solicitar())
        self.assertEqual((resumo['valor'], resumo['valor_cheio'], resumo['promocao']),
                         ('R$ 120,00', 'R$ 150,00', 'Semana Glow'))

    def test_sessao_do_pacote_nunca_mostra_o_preco_cheio(self):
        cli = criar_cliente(telefone='17988887777')
        criar_compra_pacote(cli, criar_pacote(nome='Glow', procedimento=self.proc, sessoes=3))
        Promocao.objects.create(nome='Semana Glow', procedimento=self.proc, desconto_percentual=Decimal('20'),
                                data_inicio=timezone.localdate(), data_fim=timezone.localdate() + timedelta(days=10))
        resumo = agendamento_online.resumo_confirmacao(self._solicitar(), reagendamento=True)
        self.assertEqual(resumo['valor'], 'Sessão do pacote Glow (restam 2)')
        self.assertEqual((resumo['valor_cheio'], resumo['promocao']), ('', ''))
        self.assertTrue(resumo['reagendamento'])
