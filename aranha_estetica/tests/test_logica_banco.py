"""Lado Python da logica no banco (docs/specs/padrao-sql.md §5) — roda no SQLite e no Postgres.

utils/nps (meio p/ cima, D7), utils/banco (allowlist do CALL, gating SQLite x PG), busca de
cliente (trigram/sem acento no PG, icontains no SQLite), HistoricoAlteracao/AnotacaoSessao
append-only no Python, admin da trilha so leitura com trilha de leitura e o housekeeping
(links vencidos/revogados e trilha antiga; so Postgres).
"""
from datetime import timedelta
from decimal import Decimal
from unittest import skipIf, skipUnless

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.middleware import ContextoBancoMiddleware
from aranha_estetica.models import (
    AnotacaoSessao, AvaliacaoNPS, Cliente, HistoricoAlteracao, LinkAcesso, LogAuditoria,
)
from aranha_estetica.utils import banco
from aranha_estetica.utils.busca import filtrar_clientes
from aranha_estetica.utils.nps import calcular_nps, media_nota, resumo_nps

from .factories import criar_atendimento, criar_cliente, criar_link, criar_procedimento, criar_profissional

PG = connection.vendor == 'postgresql'


class NpsMeioParaCimaTests(TestCase):
    """D7: o round() do Python e bancario (12,5 -> 12); o NPS exibido segue o Postgres (13)."""

    def test_calcular_nps(self):
        self.assertEqual(calcular_nps(1, 0, 8), 13)     # 12,5
        self.assertEqual(calcular_nps(0, 1, 8), -13)    # -12,5: empate se afasta do zero
        self.assertEqual(calcular_nps(2, 1, 3), 33)
        self.assertEqual(calcular_nps(3, 0, 3), 100)
        self.assertIsNone(calcular_nps(0, 0, 0))
        self.assertEqual(round(12.5), 12)               # o motivo da troca

    def test_media_e_resumo(self):
        self.assertEqual(media_nota(31, 4), Decimal('7.8'))   # 7,75
        self.assertIsNone(media_nota(None, 0))
        self.assertEqual(resumo_nps(AvaliacaoNPS.objects.none())['nps'], None)
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        for i, nota in enumerate([10, 8, 8, 8, 8, 8, 8, 8]):
            at = criar_atendimento(criar_cliente(), prof, proc, status='REALIZADO',
                                   data_hora=timezone.now() - timedelta(days=i + 1))
            AvaliacaoNPS.objects.create(atendimento=at, nota=nota)
        resumo = resumo_nps(AvaliacaoNPS.objects.all())
        self.assertEqual((resumo['qtd_respostas'], resumo['qtd_promotores'], resumo['qtd_neutros'],
                          resumo['qtd_detratores'], resumo['nps'], resumo['nota_media']),
                         (8, 1, 7, 0, 13, Decimal('8.3')))


class BancoUtilTests(TestCase):
    def test_chamar_so_aceita_procedure_da_allowlist_com_a_aridade_certa(self):
        with self.assertRaises(ValueError):
            banco.chamar('sp_inexistente')
        with self.assertRaises(ValueError):
            banco.chamar('sp_expirar_pacotes', None)       # aridade 2
        with self.assertRaises(ValueError):
            banco.chamar('DROP TABLE cliente; --', 1, 2)

    @skipIf(PG, 'no Postgres o CALL roda (test_lgpd_esquecimento, test_pg_paridade)')
    def test_procedure_fora_do_postgres_recusa_com_mensagem(self):
        # sem fallback Python: no SQLite o esquecimento/retencao nao "finge" que rodou
        with self.assertRaisesRegex(ImproperlyConfigured, 'docker compose up -d db'):
            banco.chamar('sp_expirar_pacotes', None, None)

    @skipIf(PG, 'no Postgres o contexto vai ao banco (test_pg_historico)')
    def test_contexto_e_middleware_sao_no_op_no_sqlite(self):
        with CaptureQueriesContext(connection) as consultas:
            banco.definir_contexto_banco(1, '10.0.0.1', 'web')
            resposta = ContextoBancoMiddleware(lambda request: 'ok')(RequestFactory().get('/'))
        self.assertEqual(resposta, 'ok')
        self.assertEqual(consultas.captured_queries, [])


class BuscaClienteTests(TestCase):
    def setUp(self):
        self.joao = criar_cliente(nome='João Ávila', email='joao@exemplo.com', telefone='17999990001')
        self.maria = criar_cliente(nome='Maria Souza', telefone='17988882222')

    def _nomes(self, termo, **kw):
        return set(filtrar_clientes(Cliente.objects.all(), termo, **kw).values_list('nome', flat=True))

    def test_nome_email_telefone(self):
        self.assertEqual(self._nomes('Ávila'), {'João Ávila'})
        self.assertEqual(self._nomes('MARIA'), {'Maria Souza'})
        self.assertEqual(self._nomes('joao@exemplo'), {'João Ávila'})
        self.assertEqual(self._nomes('joao@exemplo', incluir_email=False), set())
        self.assertEqual(self._nomes('(17) 98888-2222'), {'Maria Souza'})
        self.assertEqual(self._nomes('100%_'), set())  # curinga do LIKE nao vaza

    @skipUnless(PG, 'busca sem acento usa fn_sem_acento (0057, so Postgres)')
    def test_postgres_busca_sem_acento(self):
        self.assertEqual(self._nomes('joao avila'), {'João Ávila'})
        self.assertEqual(self._nomes('AVI'), {'João Ávila'})

    @skipUnless(PG, 'indice GIN trigram so no Postgres')
    def test_postgres_busca_usa_o_indice_trigram(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_opclass WHERE opcname = 'gin_trgm_ops')")
            if not cursor.fetchone()[0]:
                self.skipTest('Postgres sem pg_trgm real (harness local emula a extensao)')
        # _base_manager: sem o filtro de soft delete (deletado_em IS NULL), que casa com o
        # predicado de indices parciais (uq_cliente_*_ativo) e, numa tabela de 2 linhas,
        # vira um bitmap mais barato que o trigram (visto no PG 18 do CI).
        qs = filtrar_clientes(Cliente._base_manager.all(), 'avila', incluir_email=False).only('pk')
        sql, params = qs.query.sql_with_params()
        with transaction.atomic(), connection.cursor() as cursor:
            # Tabela de teste quase vazia: sem seqscan o planner ainda prefere varrer um
            # btree qualquer inteiro (visto no PG 18 do CI: uq_cliente_email_ativo + Filter).
            # Deixando so bitmap scan, o plano prova que a expressao e indexavel pelo trigram.
            cursor.execute('SET LOCAL enable_seqscan = off')
            cursor.execute('SET LOCAL enable_indexscan = off')
            cursor.execute('SET LOCAL enable_indexonlyscan = off')
            cursor.execute('EXPLAIN ' + sql, params)
            plano = '\n'.join(linha for (linha,) in cursor.fetchall())
        self.assertIn('ix_cliente_nome_trgm', plano)


class TrilhaAppendOnlyNoPythonTests(TestCase):
    def _linha(self, **campos):
        dados = {'tabela': 'preco', 'registro_pk': 1, 'operacao': 'INSERT', 'dados_depois': {'valor': 1},
                 'transacao_ref': 1, **campos}
        return HistoricoAlteracao.objects.create(**dados)

    def test_historico_alteracao_recusa_alterar_e_excluir(self):
        h = self._linha()
        h.origem = 'forjado'
        with self.assertRaises(ValidationError):
            h.save()
        with self.assertRaises(ValidationError):
            h.delete()
        with self.assertRaises(ValidationError):
            HistoricoAlteracao.objects.filter(pk=h.pk).delete()
        self.assertEqual(str(h), 'Inclusão preco #1')

    def test_checks_de_coerencia(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._linha(operacao='INSERT', dados_antes={'x': 1})
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._linha(operacao='APAGOU')

    def test_anotacao_sessao_append_only(self):
        prof = criar_profissional()
        at = criar_atendimento(criar_cliente(), prof, criar_procedimento(profissional=prof))
        nota = AnotacaoSessao.objects.create(atendimento=at, texto='Sem intercorrencias')
        nota.texto = 'forjado'
        for acao in (nota.save, nota.delete, AnotacaoSessao.objects.filter(pk=nota.pk).delete,
                     lambda: AnotacaoSessao.objects.filter(pk=nota.pk).update(texto='x')):
            with self.subTest(acao=acao), self.assertRaises(ValidationError):
                acao()
        self.assertEqual(AnotacaoSessao.objects.get(pk=nota.pk).texto, 'Sem intercorrencias')


class HistoricoAlteracaoAdminTests(TestCase):
    """Django admin: trilha so leitura (dado de saude do prontuario) e cada abertura auditada."""

    def setUp(self):
        from django.core.cache import cache

        from .test_auth_equipe import _admin, _com_totp, _token
        cache.clear()
        self.admin = _admin(email='adm-trilha@test.com')
        device = _com_totp(self.admin)
        self.c = Client()
        self.c.force_login(self.admin)
        self.c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(device)})
        self.h = HistoricoAlteracao.objects.create(
            tabela='prontuario', registro_pk=7, operacao='UPDATE', colunas_alteradas=['alergias'],
            dados_antes={'alergias': ''}, dados_depois={'alergias': 'Dipirona'}, transacao_ref=1,
        )
        self.base = '/django-admin-sv/aranha_estetica/historicoalteracao/'

    def test_so_leitura_e_leitura_auditada(self):
        self.assertEqual(self.c.get(self.base + 'add/').status_code, 403)
        resp = self.c.post(f'{self.base}{self.h.pk}/change/', {'origem': 'forjado'})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.c.post(f'{self.base}{self.h.pk}/delete/', {'post': 'yes'}).status_code, 403)
        self.assertEqual(self.c.get(f'{self.base}{self.h.pk}/change/').status_code, 200)
        log = LogAuditoria.objects.get(acao='Visualizou historico de alteracao (django-admin)')
        self.assertEqual((log.tabela, log.registro_id, log.usuario_id), ('historico_alteracao', self.h.pk,
                                                                         self.admin.pk))
        self.assertEqual(log.detalhes, {'tabela': 'prontuario', 'registro_pk': 7})
        # busca nao numerica nao quebra (Cast p/ texto) e a numerica acha
        self.assertEqual(self.c.get(self.base, {'q': 'abc'}).status_code, 200)
        self.assertContains(self.c.get(self.base, {'q': '7'}), 'prontuario')


@skipUnless(PG, 'sp_purgar_dados_expirados so no Postgres (0058)')
class HousekeepingLinksETrilhaTests(TestCase):
    """job_housekeeping: links vencidos/revogados (90 dias) e trilha antiga (5 anos; prontuario fica)
    pelo sp_purgar_dados_expirados."""

    def test_purga_links_e_trilha(self):
        from aranha_estetica.tasks_manutencao import job_housekeeping

        agora = timezone.now()
        prof = criar_profissional()
        at = criar_atendimento(criar_cliente(), prof, criar_procedimento(profissional=prof))
        criar_link('GERENCIAR_AGENDAMENTO', at, expira_em=agora - timedelta(days=100))   # sai
        vencido_recente = criar_link('GERENCIAR_AGENDAMENTO', at, expira_em=agora - timedelta(days=10))
        criar_link('GERENCIAR_AGENDAMENTO', at, expira_em=agora + timedelta(days=5))
        revogado = LinkAcesso.objects.latest('id')
        LinkAcesso.objects.filter(pk=revogado.pk).update(revogado_em=agora - timedelta(days=95))  # sai
        criar_link('FEED_ICS', prof, expira_em=None)                                             # fica
        with connection.cursor() as cursor:
            for tabela, dias in (('preco', 365 * 5 + 3), ('prontuario', 365 * 5 + 3), ('preco', 1)):
                cursor.execute(
                    'INSERT INTO historico_alteracao (tabela, registro_pk, operacao, colunas_alteradas, '
                    'dados_depois, ip_origem, origem, transacao_ref, criado_em) '
                    "VALUES (%s, 1, 'INSERT', %s, %s, NULL, '', 1, %s)",
                    [tabela, '[]', '{}', agora - timedelta(days=dias)],
                )
        antes = HistoricoAlteracao.objects.count()

        resumo = job_housekeeping.apply().get()

        self.assertEqual((resumo['links'], resumo['historico']), (2, 1))
        self.assertEqual(LinkAcesso.objects.count(), 2)  # vencido ha 10 dias + feed ICS sem validade
        self.assertIsNotNone(vencido_recente)
        self.assertEqual(HistoricoAlteracao.objects.count(), antes - 1)
        self.assertTrue(HistoricoAlteracao.objects.filter(tabela='prontuario').exists())
