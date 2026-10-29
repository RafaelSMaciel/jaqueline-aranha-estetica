"""Retencao de saude (20 anos) e de pacote (5 anos), expurgo e pseudonimizacao completa (0077).

Decisoes 33 e 34: o registro de saude e expurgado 20 anos depois do ultimo evento de saude e entao a
cliente vira candidata normal; pacote retem 5 anos depois de expirar; a pseudonimizacao zera a
indicacao e o IP do aceite LGPD (o aceite SAUDE fica enquanto houver registro de saude). Os triggers
de imutabilidade so aceitam o expurgo com a flag E 20 anos de idade. Tudo so no Postgres.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError, IntegrityError, InternalError, connection, transaction
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica import jobs_manutencao as tm
from aranha_estetica.models import (
    AceiteTermo, AnotacaoSessao, Cliente, FormularioAnamnese, ListaEspera, LogAuditoria, Pacote,
    Prontuario, ProntuarioVersao, RespostaAnamnese, VersaoTermo,
)
from aranha_estetica.services.lgpd import LgpdService
from aranha_estetica.utils.auditoria import registrar_log

from .factories import (
    criar_atendimento, criar_cliente, criar_compra_pacote, criar_link, criar_procedimento,
    criar_profissional, envelhecer_trilha,
)
from .test_auth_equipe import _admin, _com_totp, _token

PG = connection.vendor == 'postgresql'
MOTIVO = 'retencao e expurgo de saude: procedures e triggers so no Postgres (0077)'
ANO = timedelta(days=365)


def _sql(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


def _request(ip='203.0.113.20'):
    return RequestFactory().post('/', REMOTE_ADDR=ip, HTTP_USER_AGENT='Navegador da titular')


class _Cenario(TestCase):
    def setUp(self):
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)

    def _cliente(self, nome, criada_ha=22 * ANO, excluida_ha=timedelta(days=40)):
        c = criar_cliente(nome=nome)
        Cliente.all_objects.filter(pk=c.pk).update(
            criado_em=timezone.now() - criada_ha,
            deletado_em=timezone.now() - excluida_ha if excluida_ha else None,
            ativo=excluida_ha is None,
        )
        return Cliente.all_objects.get(pk=c.pk)

    def _registro_de_saude(self, cliente, ha):
        """REALIZADO, prontuario editado, versao, anotacao e ficha — tudo com a idade `ha`."""
        quando = timezone.now() - ha
        at = criar_atendimento(cliente, self.prof, self.proc, status='REALIZADO', data_hora=quando)
        pront = Prontuario.objects.create(cliente=cliente, alergias='Látex')
        Prontuario.objects.filter(pk=pront.pk).update(atualizado_em=quando)
        versao = ProntuarioVersao.objects.create(prontuario=pront, dados={'alergias': 'antiga'})
        envelhecer_trilha(ProntuarioVersao, versao.pk, quando)
        nota = AnotacaoSessao.objects.create(atendimento=at, texto='Pele sensível')
        envelhecer_trilha(AnotacaoSessao, nota.pk, quando)
        form, _ = FormularioAnamnese.objects.get_or_create(nome='Ficha de bem-estar')
        ficha = RespostaAnamnese.objects.create(formulario=form, cliente=cliente, atendimento=at,
                                                respostas_json={'gestante': 'nao'}, respondida_em=quando)
        RespostaAnamnese.objects.filter(pk=ficha.pk).update(criado_em=quando)
        aceite = AceiteTermo.registrar(cliente, VersaoTermo.saude_vigente(), _request(), at)
        return {'atendimento': at, 'prontuario': pront, 'versao': versao, 'nota': nota, 'ficha': ficha,
                'aceite_saude': aceite}


@skipUnless(PG, MOTIVO)
class JanelaDeSaudeTests(_Cenario):
    def test_saude_de_21_anos_e_expurgada_e_a_cliente_vira_candidata(self):
        antiga = self._cliente('Lia Antiga')
        reg = self._registro_de_saude(antiga, 21 * ANO)
        self.assertIn(antiga.pk, LgpdService.candidatos_retencao())

        resumo, falhas = tm._purgar_no_banco()
        self.assertEqual(falhas, [])
        self.assertEqual(resumo['saude'], 1)
        self.assertFalse(Prontuario.objects.filter(pk=reg['prontuario'].pk).exists())
        self.assertFalse(ProntuarioVersao.objects.filter(pk=reg['versao'].pk).exists())
        self.assertFalse(AnotacaoSessao.objects.filter(pk=reg['nota'].pk).exists())
        self.assertFalse(RespostaAnamnese.objects.filter(pk=reg['ficha'].pk).exists())
        aceite = AceiteTermo.objects.get(pk=reg['aceite_saude'].pk)   # a prova fica, sem IP/user-agent
        self.assertEqual((aceite.ip, aceite.user_agent), (None, ''))
        # a trilha nao guardou copia do prontuario expurgado; o expurgo ficou registrado
        self.assertFalse(_trilha_do_prontuario(reg['prontuario'].pk, operacao='DELETE'))
        self.assertTrue(LogAuditoria.objects.filter(acao__startswith='Expurgo do registro de saude').exists())

        self.assertEqual(LgpdService.aplicar_retencao()['clientes'], 1)
        self.assertTrue(Cliente.all_objects.get(pk=antiga.pk).nome.startswith('[ANONIMIZADO-'))

    def test_realizado_ha_10_anos_continua_retido(self):
        cliente = self._cliente('Rosa Dez Anos')
        reg = self._registro_de_saude(cliente, 10 * ANO)
        self.assertNotIn(cliente.pk, LgpdService.candidatos_retencao())
        resumo, _falhas = tm._purgar_no_banco()
        self.assertEqual(resumo['saude'], 0)
        self.assertTrue(Prontuario.objects.filter(pk=reg['prontuario'].pk).exists())

    def test_ficha_recente_segura_o_registro_antigo(self):
        cliente = self._cliente('Ficha Recente')
        reg = self._registro_de_saude(cliente, 21 * ANO)
        form = FormularioAnamnese.objects.get(nome='Ficha de bem-estar')
        RespostaAnamnese.objects.create(formulario=form, cliente=cliente, respostas_json={'x': 'y'})
        resumo, _falhas = tm._purgar_no_banco()
        self.assertEqual(resumo['saude'], 0)
        self.assertTrue(Prontuario.objects.filter(pk=reg['prontuario'].pk).exists())

    def test_pacote_expirado_ha_6_anos_nao_segura_e_ha_2_anos_segura(self):
        pacote = Pacote.objects.create(nome='Glow', preco_total=Decimal('100'), validade_meses=1)
        velha = self._cliente('Pacote Velho', criada_ha=8 * ANO)
        recente = self._cliente('Pacote Recente', criada_ha=8 * ANO)
        for cliente, expirou_ha in ((velha, 6 * ANO), (recente, 2 * ANO)):
            compra = criar_compra_pacote(cliente, pacote)
            type(compra).objects.filter(pk=compra.pk).update(
                data_expiracao=timezone.localdate() - expirou_ha, status='EXPIRADO',
                criado_em=timezone.now() - expirou_ha - ANO,
            )
        candidatos = set(LgpdService.candidatos_retencao())
        self.assertIn(velha.pk, candidatos)
        self.assertNotIn(recente.pk, candidatos)


@skipUnless(PG, MOTIVO)
class PseudonimizacaoCompletaTests(_Cenario):
    def test_zera_indicacao_ip_do_aceite_lgpd_espera_anonima_e_detalhes_lgpd(self):
        madrinha = criar_cliente(nome='Madrinha')
        titular = criar_cliente(nome='Titular Completa', telefone='17955550001', indicado_por=madrinha)
        self.assertTrue(titular.codigo_indicacao)
        aceite_lgpd = AceiteTermo.registrar(titular, VersaoTermo.lgpd_vigente(), _request())
        reg = self._registro_de_saude(titular, timedelta(days=30))
        anonima = ListaEspera.objects.create(nome_contato='Titular', telefone_contato='17955550001',
                                             procedimento=self.proc, data_desejada=timezone.localdate())
        registrar_log(None, 'LGPD: pedido de correcao', 'cliente', titular.pk, detalhes={'prazo': '2026-11-12'})

        LgpdService.esquecer_cliente(titular)

        salvo = Cliente.all_objects.get(pk=titular.pk)
        self.assertEqual((salvo.codigo_indicacao, salvo.indicado_por_id), (None, None))
        lgpd = AceiteTermo.objects.get(pk=aceite_lgpd.pk)
        self.assertEqual((lgpd.ip, lgpd.user_agent), (None, ''))
        saude = AceiteTermo.objects.get(pk=reg['aceite_saude'].pk)   # ha registro de saude: a prova fica
        self.assertEqual(saude.ip, '203.0.113.20')
        self.assertFalse(ListaEspera.objects.filter(pk=anonima.pk).exists())
        self.assertIsNone(LogAuditoria.objects.get(acao='LGPD: pedido de correcao').detalhes)

    def test_sem_registro_de_saude_o_aceite_saude_tambem_perde_o_ip(self):
        titular = criar_cliente(nome='Sem Saude')
        aceite = AceiteTermo.registrar(titular, VersaoTermo.saude_vigente(), _request())
        LgpdService.esquecer_cliente(titular)
        aceite.refresh_from_db()
        self.assertEqual((aceite.ip, aceite.user_agent), (None, ''))


def _trilha_do_prontuario(pk, operacao):
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM historico_alteracao WHERE tabela = 'prontuario' "
                       'AND registro_pk = %s AND operacao = %s', [pk, operacao])
        return cursor.fetchone()[0]


@skipUnless(PG, MOTIVO)
class ImutabilidadeComExpurgoTests(_Cenario):
    """O expurgo abre so a porta certa: flag local da transacao E 20 anos de idade."""

    def setUp(self):
        super().setUp()
        self.cliente = criar_cliente(nome='Imutavel')
        self.reg = self._registro_de_saude(self.cliente, timedelta(days=30))

    def _apagar(self, tabela, pk, *, flag):
        with transaction.atomic():
            if flag:
                _sql("SELECT set_config('app.expurgo_autorizado', 'on', true)")
            _sql(f'DELETE FROM {tabela} WHERE id = %s', [pk])

    def test_versao_e_anotacao_recentes_nao_saem_nem_com_a_flag(self):
        for tabela, pk in (('prontuario_versao', self.reg['versao'].pk), ('anotacao_sessao', self.reg['nota'].pk)):
            for flag in (False, True):
                with self.subTest(tabela=tabela, flag=flag), self.assertRaises(IntegrityError):
                    self._apagar(tabela, pk, flag=flag)

    def test_com_20_anos_so_saem_com_a_flag(self):
        velha = timezone.now() - 21 * ANO
        envelhecer_trilha(ProntuarioVersao, self.reg['versao'].pk, velha)
        with self.assertRaises(IntegrityError):
            self._apagar('prontuario_versao', self.reg['versao'].pk, flag=False)
        self._apagar('prontuario_versao', self.reg['versao'].pk, flag=True)
        self.assertFalse(ProntuarioVersao.objects.filter(pk=self.reg['versao'].pk).exists())

    def test_anotacao_continua_sem_update(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            _sql("SELECT set_config('app.expurgo_autorizado', 'on', true)")
            _sql("UPDATE anotacao_sessao SET texto = 'x' WHERE id = %s", [self.reg['nota'].pk])

    def test_aceite_so_aceita_minimizar(self):
        pk = self.reg['aceite_saude'].pk
        for sql in ("UPDATE aceite_termo SET ip_origem = '10.9.9.9' WHERE id = %s",
                    "UPDATE aceite_termo SET user_agent = 'outro' WHERE id = %s",
                    "UPDATE aceite_termo SET conteudo_hash = repeat('0', 64) WHERE id = %s",
                    'DELETE FROM aceite_termo WHERE id = %s'):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                _sql(sql, [pk])
        _sql("UPDATE aceite_termo SET ip_origem = NULL, user_agent = '' WHERE id = %s", [pk])
        self.assertIsNone(AceiteTermo.objects.get(pk=pk).ip)

    def test_prazo_de_saude_menor_que_20_anos_falha_a_etapa(self):
        antiga = self._cliente('Prazo Curto')
        self._registro_de_saude(antiga, 12 * ANO)
        with self.settings(RETENCAO_ATENDIMENTO_SAUDE_DIAS=365 * 10):
            resumo, falhas = tm._purgar_no_banco()
        self.assertEqual(falhas, ['saude'])
        self.assertTrue(Prontuario.objects.filter(cliente=antiga).exists())


@skipUnless(PG, MOTIVO)
class AcaoPseudonimizarAdminTests(TestCase):
    URL = '/django-admin-sv/aranha_estetica/cliente/'

    def setUp(self):
        self.admin = _admin()
        self.device = _com_totp(self.admin)
        self.c = Client()
        self.c.force_login(self.admin)
        self.a = criar_cliente(nome='Ana Lote')
        self.b = criar_cliente(nome='Bia Lote')
        self.token_a = criar_link('DESCADASTRO', self.a)

    def _post(self):
        return self.c.post(self.URL, {
            'action': 'acao_pseudonimizar_lgpd', '_selected_action': [self.a.pk, self.b.pk],
        }, follow=True)

    def _verificar_2fa(self):
        self.c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(self.device)})

    def test_caminho_feliz(self):
        self._verificar_2fa()
        resp = self._post()
        self.assertContains(resp, '2 cliente(s) pseudonimizado(s)')
        for cliente in (self.a, self.b):
            self.assertTrue(Cliente.all_objects.get(pk=cliente.pk).nome.startswith('[ANONIMIZADO-'))
        self.assertIsNone(LgpdService.cliente_por_token_descadastro(self.token_a))
        self.assertEqual(LogAuditoria.objects.filter(acao__startswith='Cliente anonimizado',
                                                     usuario=self.admin).count(), 2)

    def test_atomico_a_segunda_falha_desfaz_a_primeira(self):
        self._verificar_2fa()
        original = LgpdService.esquecer_cliente.__func__
        chamadas = []

        def segunda_falha(cls, cliente, usuario=None, request=None):
            chamadas.append(cliente.pk)
            if len(chamadas) == 2:
                raise InternalError('falha simulada')
            return original(cls, cliente, usuario=usuario, request=request)

        with patch.object(LgpdService, 'esquecer_cliente', classmethod(segunda_falha)), \
                self.assertRaises(DatabaseError):
            self._post()
        self.assertEqual(len(chamadas), 2)
        self.assertEqual(sorted(Cliente.all_objects.filter(pk__in=[self.a.pk, self.b.pk])
                                .values_list('nome', flat=True)), ['Ana Lote', 'Bia Lote'])

    def test_sem_2fa_ninguem_e_pseudonimizado(self):
        self._post()
        self.assertEqual(sorted(Cliente.all_objects.filter(pk__in=[self.a.pk, self.b.pk])
                                .values_list('nome', flat=True)), ['Ana Lote', 'Bia Lote'])


class AcaoExportarCompletoAdminTests(TestCase):
    """Pedido de acesso da titular entregue pela clinica: export completo, uma por vez, na trilha."""
    URL = '/django-admin-sv/aranha_estetica/cliente/'

    def setUp(self):
        self.admin = _admin()
        self.device = _com_totp(self.admin)
        self.c = Client()
        self.c.force_login(self.admin)
        self.c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(self.device)})
        self.a = criar_cliente(nome='Ana Export')
        self.b = criar_cliente(nome='Bia Export')

    def test_exporta_uma_titular_com_saude_e_registra(self):
        from aranha_estetica.models import LogAuditoria
        resp = self.c.post(self.URL, {'action': 'acao_exportar_dados_completos', '_selected_action': [self.a.pk]})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('attachment; filename="dados-cliente-', resp['Content-Disposition'])
        self.assertIn('Ana Export', resp.content.decode())
        self.assertTrue(LogAuditoria.objects.filter(
            acao='LGPD: exportou dados completos (com saúde)', registro_id=self.a.pk).exists())

    def test_mais_de_uma_recusa(self):
        resp = self.c.post(self.URL, {'action': 'acao_exportar_dados_completos',
                                      '_selected_action': [self.a.pk, self.b.pk]}, follow=True)
        self.assertContains(resp, 'Selecione exatamente uma cliente')
