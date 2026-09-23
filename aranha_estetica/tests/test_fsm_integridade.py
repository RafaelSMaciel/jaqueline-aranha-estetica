"""Regressao de integridade dos models (auditoria pre-producao, pacote DB)."""
import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from aranha_estetica.models import (
    AnotacaoSessao, Atendimento, BloqueioAgenda, Carteira, Cliente, CodigoOtp,
    Configuracao, ConsumoSessao, ListaEspera, LogAuditoria, MovimentoCarteira,
    Notificacao, Preco, Prontuario, RegraComissao, Usuario,
)

from .factories import (
    criar_atendimento, criar_cliente, criar_compra_pacote, criar_pacote,
    criar_procedimento, criar_profissional,
)


class FsmAtendimentoTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.at = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')

    def test_transicao_valida_contra_status_do_banco(self):
        # 2 requisicoes com a mesma linha carregada (duplo clique / link + recepcao)
        copia = Atendimento.objects.get(pk=self.at.pk)
        self.at.cancelar(motivo='cliente desistiu')
        with self.assertRaises(Atendimento.TransicaoInvalida):
            copia.marcar_realizado()
        self.assertEqual(copia.status, 'CANCELADO')  # memoria sincronizada com o banco
        self.at.refresh_from_db()
        self.assertEqual(self.at.status, 'CANCELADO')
        # so a transicao valida foi auditada
        self.assertEqual(
            LogAuditoria.objects.filter(tabela='atendimento', registro_id=self.at.pk).count(), 1,
        )

    def test_transicao_valida_grava_e_audita(self):
        self.at.confirmar()
        self.at.marcar_realizado()
        self.at.refresh_from_db()
        self.assertEqual(self.at.status, 'REALIZADO')

    def test_clean_bloqueia_sobreposicao_ativa(self):
        outro = Atendimento(
            cliente=criar_cliente(), profissional=self.prof, procedimento=self.proc,
            data_hora_inicio=self.at.data_hora_inicio + timedelta(minutes=10),
            data_hora_fim=self.at.data_hora_fim + timedelta(minutes=10),
            status='PENDENTE',
        )
        with self.assertRaises(ValidationError):
            outro.clean()
        # inativo (cancelado) nao conflita; fim antes do inicio e erro de campo
        outro.status = 'CANCELADO'
        outro.clean()
        outro.data_hora_fim = outro.data_hora_inicio - timedelta(minutes=1)
        with self.assertRaises(ValidationError):
            outro.clean()

    def test_um_retorno_vivo_por_origem(self):
        self.at.marcar_realizado()
        base = self.at.data_hora_inicio + timedelta(days=15)

        def retorno(delta_h, status='PENDENTE'):
            return Atendimento.objects.create(
                cliente=self.cliente, profissional=self.prof, procedimento=self.proc,
                data_hora_inicio=base + timedelta(hours=delta_h),
                data_hora_fim=base + timedelta(hours=delta_h, minutes=30),
                status=status, eh_retorno=True, atendimento_origem=self.at, valor_cobrado=0,
            )

        primeiro = retorno(0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            retorno(2)
        # cancelado libera um novo retorno
        primeiro.cancelar(motivo='remarcar')
        retorno(4)

    def test_factory_nao_sobrepoe_atendimentos_ativos(self):
        segundo = criar_atendimento(criar_cliente(), self.prof, self.proc)
        self.assertGreaterEqual(segundo.data_hora_inicio, self.at.data_hora_fim)


class ProtecaoHistoricoTests(TestCase):
    """on_delete PROTECT: o Collector barra antes do trigger do ledger (PG) virar 500."""

    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)

    def test_atendimento_com_movimento_de_carteira_nao_e_apagado(self):
        cliente = criar_cliente()
        at = criar_atendimento(cliente, self.prof, self.proc, status='CANCELADO')
        carteira = Carteira.objects.create(cliente=cliente, saldo=Decimal('10'))
        MovimentoCarteira.objects.create(
            carteira=carteira, tipo='CREDITO', origem='CASHBACK_ESTORNO',
            valor=Decimal('10'), saldo_resultante=Decimal('10'), atendimento=at,
        )
        with self.assertRaises(ProtectedError):
            at.delete()

    def test_hard_delete_de_cliente_com_historico_e_barrado(self):
        cliente = criar_cliente()
        Prontuario.objects.create(cliente=cliente, alergias='nenhuma')
        Carteira.objects.create(cliente=cliente)
        with self.assertRaises(ProtectedError):
            Cliente.all_objects.filter(pk=cliente.pk).delete()
        self.assertTrue(Cliente.all_objects.filter(pk=cliente.pk).exists())


class ClienteTests(TestCase):
    def test_save_normaliza_ddi_e_zero(self):
        c = criar_cliente(telefone='+55 (17) 99999-0001')
        self.assertEqual(c.telefone, '17999990001')
        c2 = criar_cliente(telefone='017 3333-0000', cpf='529.982.247-25')
        self.assertEqual(c2.telefone, '1733330000')
        self.assertEqual(c2.cpf, '52998224725')

    def test_limite_de_faltas_vem_da_configuracao(self):
        Configuracao.objects.create(chave='MAX_FALTAS_BLOQUEIO', valor='2')
        c = criar_cliente()
        c.registrar_falta()
        self.assertFalse(c.bloqueado_online)
        c.registrar_falta()
        self.assertTrue(c.bloqueado_online)

    def test_limite_de_faltas_invalido_usa_padrao(self):
        Configuracao.objects.create(chave='MAX_FALTAS_BLOQUEIO', valor='abc')
        self.assertEqual(Cliente.limite_faltas_bloqueio(), 3)


class BloqueioRecorrenteTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional()

    def _bloqueio(self, h_ini, h_fim):
        tz = timezone.get_current_timezone()
        ini = timezone.make_aware(datetime(2026, 10, 7, h_ini, 0), tz)  # quarta
        return BloqueioAgenda.objects.create(
            profissional=self.prof, data_hora_inicio=ini,
            data_hora_fim=ini.replace(hour=h_fim), regra_recorrencia='FREQ=WEEKLY;BYDAY=WE',
            recorrencia_ate=date(2026, 10, 28),
        )

    def _dias_locais(self, ocorrencias):
        return [(timezone.localtime(i).day, timezone.localtime(i).weekday()) for i, _ in ocorrencias]

    def test_recorrencia_ate_e_inclusivo(self):
        b = self._bloqueio(9, 12)
        tz = timezone.get_current_timezone()
        occ = b.expandir_ocorrencias(timezone.make_aware(datetime(2026, 10, 1), tz),
                                     timezone.make_aware(datetime(2026, 11, 10), tz))
        self.assertEqual(self._dias_locais(occ), [(7, 2), (14, 2), (21, 2), (28, 2)])

    def test_byday_avaliado_no_fuso_local(self):
        # 21h-23h BRT = dia seguinte em UTC: tem que continuar caindo na quarta
        b = self._bloqueio(21, 23)
        tz = timezone.get_current_timezone()
        occ = b.expandir_ocorrencias(timezone.make_aware(datetime(2026, 10, 1), tz),
                                     timezone.make_aware(datetime(2026, 11, 10), tz))
        self.assertEqual(self._dias_locais(occ), [(7, 2), (14, 2), (21, 2), (28, 2)])
        self.assertTrue(all(timezone.localtime(i).hour == 21 for i, _ in occ))


class CodigoOtpHashTests(TestCase):
    def test_hash_hmac_sem_sha256_puro(self):
        codigo, obj = CodigoOtp.gerar('a@x.com')
        self.assertNotEqual(obj.codigo_hash, hashlib.sha256(codigo.encode()).hexdigest())
        self.assertEqual(len(obj.codigo_hash), 64)
        ok, _ = CodigoOtp.verificar('a@x.com', codigo)
        self.assertTrue(ok)

    def test_pseudo_email_de_telefone_normaliza_ddi(self):
        self.assertEqual(
            CodigoOtp.email_para_telefone('+55 (17) 99999-0001'),
            CodigoOtp.email_para_telefone('17999990001'),
        )


class AnotacaoAutoriaTests(TestCase):
    """Registro clinico com autor: excluir o usuario nao apaga a autoria."""

    def setUp(self):
        prof = criar_profissional()
        self.at = criar_atendimento(criar_cliente(), prof, criar_procedimento(profissional=prof))
        self.autor = Usuario.objects.create_user('dra@x.com', 'SenhaForte!2026', nome='Dra. Clara')

    def test_autor_nome_gravado_na_escrita(self):
        nota = AnotacaoSessao.objects.create(atendimento=self.at, autor=self.autor, texto='Sem intercorrencias')
        self.autor.nome = 'Clara Renomeada'
        self.autor.save()
        nota.refresh_from_db()
        self.assertEqual(nota.autor_nome, 'Dra. Clara')

    def test_excluir_autor_com_anotacao_e_barrado(self):
        AnotacaoSessao.objects.create(atendimento=self.at, autor=self.autor, texto='Nota')
        with self.assertRaises(ProtectedError):
            self.autor.delete()
        self.assertEqual(AnotacaoSessao.objects.get().autor_id, self.autor.pk)


class RegraComissaoPercentualTests(TestCase):
    def test_percentual_acima_de_100_recusado(self):
        with self.assertRaises(ValidationError):
            RegraComissao(percentual=Decimal('150')).full_clean()
        RegraComissao(percentual=Decimal('100')).full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            RegraComissao.objects.create(percentual=Decimal('150'))


class DefaultsRodada2Tests(TestCase):
    def test_papel_default_e_profissional(self):
        # RECEPCAO nao tem login; ADMIN nunca e implicito
        self.assertEqual(Usuario(email='x@x.com', nome='X').papel, Usuario.PAPEL_PROFISSIONAL)

    def test_vigencia_do_preco_usa_data_local(self):
        self.assertIs(Preco._meta.get_field('vigente_desde').default, timezone.localdate)

    def test_lista_espera_guarda_email_de_contato(self):
        esp = ListaEspera.objects.create(
            cliente=criar_cliente(), procedimento=criar_procedimento(),
            data_desejada=timezone.localdate(), email_contato='contato@x.com',
        )
        esp.refresh_from_db()
        self.assertEqual(esp.email_contato, 'contato@x.com')

    def test_notificacao_tipo_termo(self):
        prof = criar_profissional()
        at = criar_atendimento(criar_cliente(), prof, criar_procedimento(profissional=prof))
        notif = Notificacao.objects.create(atendimento=at, tipo='TERMO', canal='EMAIL', token='t' * 20)
        self.assertEqual(notif.get_tipo_display(), 'Termo de consentimento')


class SaldoPacoteTests(TestCase):
    def test_saldo_por_procedimento_conta_consumos(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cliente = criar_cliente()
        compra = criar_compra_pacote(cliente, criar_pacote(procedimento=proc, sessoes=5))
        at = criar_atendimento(cliente, prof, proc)
        ConsumoSessao.objects.create(compra_pacote=compra, atendimento=at)

        saldo = compra.saldo_por_procedimento()
        self.assertEqual(len(saldo), 1)
        self.assertEqual(saldo[0]['procedimento'], proc)
        self.assertEqual((saldo[0]['total'], saldo[0]['usadas'], saldo[0]['restantes']), (5, 1, 4))
        self.assertEqual(compra.sessoes_restantes(), 4)
