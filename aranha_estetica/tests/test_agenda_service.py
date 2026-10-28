"""services.agenda + excecoes: a regra unica de horario das 5 portas (wizard, painel, portal,
calendario, retorno) e as recusas tipadas."""
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from aranha_estetica.excecoes import (
    HorarioIndisponivel, RegraDeNegocio, TransicaoInvalida, eh_sobreposicao,
)
from aranha_estetica.models import AgendaExcecao, Atendimento, FormularioAnamnese, RespostaAnamnese
from aranha_estetica.services import agenda

from .factories import (
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional, limpar_feriados,
)


def _local(dias=3, hora=10):
    return timezone.localtime(timezone.now() + timedelta(days=dias)).replace(
        hour=hora, minute=0, second=0, microsecond=0)


class _Base(TestCase):
    def setUp(self):
        limpar_feriados()
        self.prof = criar_profissional()   # 7 dias 09h-18h
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('200.00'))
        self.cli = criar_cliente()


class ExcecoesTests(TestCase):
    def test_transicao_do_model_e_a_regra_de_negocio_comum(self):
        self.assertIs(Atendimento.TransicaoInvalida, TransicaoInvalida)
        self.assertTrue(issubclass(TransicaoInvalida, RegraDeNegocio))

    def test_codigo_padrao_e_sobrescrito(self):
        self.assertEqual(HorarioIndisponivel('x').codigo, 'horario_indisponivel')
        exc = HorarioIndisponivel('x', codigo='feriado')
        self.assertEqual((exc.mensagem, exc.codigo), ('x', 'feriado'))

    def test_eh_sobreposicao(self):
        self.assertTrue(eh_sobreposicao(IntegrityError('violates ex_atendimento_sobreposicao')))
        self.assertFalse(eh_sobreposicao(IntegrityError('uq_cliente_telefone_ativo')))

    def test_fsm_recusa_com_mensagem_legivel(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        at = criar_atendimento(criar_cliente(), prof, proc, status='REALIZADO')
        with self.assertRaises(TransicaoInvalida) as ctx:
            at.confirmar()
        self.assertEqual(ctx.exception.mensagem, 'Não é possível passar de realizado para confirmado.')


class ValidarHorarioTests(_Base):
    def _codigo(self, inicio, **kw):
        with self.assertRaises(RegraDeNegocio) as ctx:
            agenda.validar_horario(self.prof, self.proc, inicio, **kw)
        return ctx.exception

    def test_horario_livre_passa(self):
        agenda.validar_horario(self.prof, self.proc, _local())

    def test_profissional_nao_habilitado(self):
        outra = criar_profissional('Dra. Outra')   # self.proc tem habilitacao so da self.prof
        with self.assertRaises(RegraDeNegocio) as ctx:
            agenda.validar_horario(outra, self.proc, _local())
        self.assertEqual(ctx.exception.codigo, 'nao_habilitado')

    def test_passado_e_feriado(self):
        self.assertEqual(self._codigo(timezone.now() - timedelta(hours=1)).codigo, 'horario_passado')
        dia = _local(4)
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=None, origem='FERIADO_NACIONAL',
                                     data_inicio=dia.date(), data_fim=dia.date(), motivo='Feriado X')
        exc = self._codigo(dia)
        self.assertEqual(exc.codigo, 'feriado')
        self.assertIn('feriado', exc.mensagem)

    def test_site_ve_mensagem_generica_equipe_ve_o_motivo(self):
        criar_atendimento(criar_cliente('Ocupa'), self.prof, self.proc, data_hora=_local())
        self.assertEqual(self._codigo(_local()).mensagem, agenda.MSG_INDISPONIVEL)
        self.assertIn('já existe um atendimento', self._codigo(_local(), detalhado=True).mensagem)
        self.assertIn('Fora do horário', self._codigo(_local(hora=20), detalhado=True).mensagem)

    def test_bloqueio_e_folga_detalhados(self):
        alvo = _local(5, 12)
        AgendaExcecao.objects.create(tipo='BLOQUEIO', profissional=self.prof,
                                     data_hora_inicio=alvo, data_hora_fim=alvo + timedelta(hours=1))
        self.assertIn('bloqueio', self._codigo(alvo, detalhado=True).mensagem)
        folga = _local(6)
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=self.prof,
                                     data_inicio=folga.date(), data_fim=folga.date(), motivo='Folga')
        self.assertIn('folga', self._codigo(folga, detalhado=True).mensagem)


class ReservarTests(_Base):
    def test_cria_com_o_preco_da_data(self):
        at = agenda.reservar(cliente=self.cli, profissional=self.prof, procedimento=self.proc,
                             inicio=_local(), status=Atendimento.STATUS_PENDENTE)
        self.assertEqual(at.valor_cobrado, Decimal('200.00'))
        self.assertIsNone(at.promocao)
        self.assertEqual(at.data_hora_fim - at.data_hora_inicio, timedelta(minutes=30))

    def test_retorno_usa_a_duracao_de_retorno(self):
        self.proc.duracao_retorno_minutos = 15
        self.proc.save()
        origem = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(-2), status='REALIZADO')
        at = agenda.reservar(cliente=self.cli, profissional=self.prof, procedimento=self.proc,
                             inicio=_local(), status=Atendimento.STATUS_PENDENTE, eh_retorno=True,
                             atendimento_origem=origem,
                             preco={'valor_cobrado': 0, 'valor_original': 0, 'promocao': None,
                                    'descricao_preco': 'Retorno'})
        self.assertEqual(at.data_hora_fim - at.data_hora_inicio, timedelta(minutes=15))
        self.assertTrue(at.eh_retorno)

    def test_janela_ocupada_recusa(self):
        criar_atendimento(criar_cliente('Ocupa'), self.prof, self.proc, data_hora=_local())
        with self.assertRaises(HorarioIndisponivel) as ctx:
            agenda.reservar(cliente=self.cli, profissional=self.prof, procedimento=self.proc,
                            inicio=_local() + timedelta(minutes=15), status=Atendimento.STATUS_PENDENTE)
        self.assertEqual(ctx.exception.codigo, 'horario_ocupado')
        self.assertFalse(Atendimento.objects.filter(cliente=self.cli).exists())


class ReagendarTests(_Base):
    def test_antigo_reagendado_novo_herda_preco_e_ficha(self):
        antigo = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(), status='CONFIRMADO')
        Atendimento.objects.filter(pk=antigo.pk).update(
            valor_cobrado=Decimal('90.00'), valor_original=Decimal('200.00'), descricao_preco='Combinado')
        antigo.refresh_from_db()
        form = FormularioAnamnese.objects.create(nome='Ficha', tipo='ANAMNESE', escopo='PROCEDIMENTO',
                                                 procedimento=self.proc, schema_json=[])
        RespostaAnamnese.objects.create(formulario=form, cliente=self.cli, atendimento=antigo, respostas_json={})

        novo = agenda.reagendar(antigo, _local(4, 11), self.prof)

        antigo.refresh_from_db()
        self.assertEqual(antigo.status, Atendimento.STATUS_REAGENDADO)
        self.assertEqual(novo.reagendado_de_id, antigo.pk)
        self.assertEqual(novo.status, Atendimento.STATUS_AGENDADO)      # aprovado + mesma profissional
        self.assertEqual((novo.valor_cobrado, novo.descricao_preco), (Decimal('90.00'), 'Combinado'))
        self.assertEqual(RespostaAnamnese.objects.get().atendimento_id, novo.pk)

    def test_outra_profissional_volta_para_a_fila(self):
        outra = criar_profissional('Dra. Outra')
        from aranha_estetica.models import Habilitacao
        Habilitacao.objects.create(profissional=outra, procedimento=self.proc)
        antigo = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(), status='AGENDADO')
        novo = agenda.reagendar(antigo, _local(4, 11), outra)
        self.assertEqual(novo.status, Atendimento.STATUS_PENDENTE)

    def test_pode_sobrepor_o_proprio_horario_antigo(self):
        proc_longo = criar_procedimento('Longo', duracao=60, profissional=self.prof)
        antigo = criar_atendimento(self.cli, self.prof, proc_longo, data_hora=_local(), status='AGENDADO')
        # grade de 30 min: 10:30-11:30 sobrepoe o proprio 10:00-11:00
        novo = agenda.reagendar(antigo, antigo.data_hora_inicio + timedelta(minutes=30), self.prof)
        self.assertEqual(novo.data_hora_inicio, antigo.data_hora_inicio + timedelta(minutes=30))

    def test_finalizado_recusa_e_nada_muda(self):
        antigo = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(), status='AGENDADO')
        Atendimento.objects.filter(pk=antigo.pk).update(status='CANCELADO')
        with self.assertRaises(TransicaoInvalida):
            agenda.reagendar(antigo, _local(4, 11), self.prof)
        self.assertEqual(Atendimento.objects.count(), 1)


class MoverTests(_Base):
    def test_move_no_lugar_e_mantem_duracao(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local())
        movido, antigo_inicio = agenda.mover(at.pk, _local(4, 15))
        self.assertEqual(antigo_inicio, _local())
        self.assertEqual(movido.pk, at.pk)
        self.assertEqual(movido.data_hora_fim - movido.data_hora_inicio, timedelta(minutes=30))

    def test_recusas(self):
        with self.assertRaises(RegraDeNegocio) as ctx:
            agenda.mover(999999, _local())
        self.assertEqual(ctx.exception.codigo, 'nao_encontrado')
        at = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(), status='REALIZADO')
        with self.assertRaises(TransicaoInvalida):
            agenda.mover(at.pk, _local(4, 15))
