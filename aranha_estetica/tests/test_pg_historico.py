"""Trilha de dados (historico_alteracao) e triggers da L1 — so Postgres (docs/specs/padrao-sql.md §5.2/§5.3).

O SQLite nao tem triggers: aqui fica a prova de que (1) o request autenticado grava o autor e o IP
na trilha, (2) o contexto e limpo no fim do request (request anonimo NA MESMA CONEXAO sai sem
autor — o test client roda na mesma thread/conexao do teste), (3) UPDATE so de atualizado_em nao
gera linha, (4) senha_hash e redigida, (5) a trilha e append-only (so a purga com a flag local
apaga) e (6) cron/Celery/conexao nova definem/limpam app.origem.
"""
import os
from io import StringIO
from datetime import timedelta
from decimal import Decimal
from unittest import mock, skipUnless

from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.middleware import ContextoBancoMiddleware
from aranha_estetica.models import (
    AnotacaoSessao, Atendimento, CompraPacote, HistoricoAlteracao, LogAuditoria, Preco, Prontuario, Usuario,
)
from aranha_estetica.utils.banco import definir_contexto_banco, limpar_contexto_conexao_nova
from aranha_estetica.utils.cifragem import decifrar_profundo

from .factories import (
    criar_atendimento, criar_cliente, criar_compra_pacote, criar_pacote, criar_procedimento,
    criar_profissional, envelhecer_trilha, limpar_feriados,
)
from .test_confirmar_agendamento import slot_local, verificar_sessao

PG = connection.vendor == 'postgresql'


def _contexto():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_setting('app.usuario_id', true), current_setting('app.ip_origem', true), "
            "current_setting('app.origem', true)"
        )
        return tuple(v or '' for v in cursor.fetchone())


def _sql(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


@skipUnless(PG, 'trilha historico_alteracao: triggers so existem no Postgres (0056)')
@override_settings(RATELIMIT_ENABLE=False)
class TrilhaDoRequestTests(TestCase):
    def setUp(self):
        cache.clear()
        limpar_feriados()
        self.admin = Usuario.objects.create_user(
            email='admin@trilha.com', password='senha-forte-2026', papel=Usuario.PAPEL_ADMIN, nome='Dra. Admin',
        )
        self.equipe = Client()
        self.equipe.force_login(self.admin)
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('120.00'))

    def test_prontuario_logado_e_agendamento_anonimo_na_mesma_conexao(self):
        cli = criar_cliente(nome='Carla Trilha')
        url = reverse('aranha:prontuario_salvar', args=[cli.pk])
        resp = self.equipe.post(url, {'alergias': 'Dipirona', 'versao': ''}, REMOTE_ADDR='203.0.113.7')
        self.assertEqual(resp.status_code, 302)
        pront = Prontuario.objects.get(cliente=cli)
        linhas = list(HistoricoAlteracao.objects.do_registro('prontuario', pront.pk))
        self.assertTrue(linhas)
        for h in linhas:  # get_or_create (INSERT) + save (UPDATE), ambos do mesmo request
            self.assertEqual(h.usuario_autor_id, self.admin.pk)
            self.assertEqual(h.ip_origem, '203.0.113.7')
            self.assertEqual(h.origem, 'web')
        self.assertEqual(len({h.transacao_ref for h in linhas}), 1)  # uma transacao (o atomic da view)
        ultima = linhas[0]
        # dado de saude cifrado tambem na trilha (0060): o antes/depois so abre com a chave
        self.assertNotIn('Dipirona', str(ultima.dados_depois))
        depois = decifrar_profundo(ultima.dados_depois)
        if ultima.operacao == HistoricoAlteracao.OPERACAO_UPDATE:
            self.assertEqual(ultima.colunas_alteradas, ['alergias'])
            self.assertEqual(ultima.dados_antes, {'alergias': ''})
            self.assertEqual(depois, {'alergias': 'Dipirona'})
        else:
            self.assertEqual(depois['alergias'], 'Dipirona')
        self.assertNotIn('atualizado_em', ultima.dados_depois)  # ignorada
        # contexto limpo no fim do request (CONN_MAX_AGE: a conexao volta p/ o proximo)
        self.assertEqual(_contexto(), ('', '', ''))

        # request ANONIMO na mesma conexao: agendamento publico -> trilha sem autor
        anonimo = Client()
        verificar_sessao(anonimo, '17999991111')
        resp = anonimo.post(reverse('aranha:confirmar_agendamento'), {
            'nome': 'Maria Publica', 'telefone': '17999991111', 'data_nascimento': '1990-06-15',
            'procedimento': self.proc.pk, 'profissional': self.prof.pk, 'datetime': slot_local(),
            'aceite_politica': 'on',
        }, REMOTE_ADDR='198.51.100.9')
        self.assertEqual(resp.status_code, 302)
        atd = Atendimento.objects.get(cliente__telefone='17999991111')
        h = HistoricoAlteracao.objects.do_registro('atendimento', atd.pk).filter(operacao='INSERT').get()
        self.assertIsNone(h.usuario_autor_id)
        self.assertEqual((h.ip_origem, h.origem), ('198.51.100.9', 'web'))
        self.assertEqual(h.dados_depois['status'], 'PENDENTE')
        self.assertEqual(_contexto(), ('', '', ''))

    def test_cron_grava_origem_do_job(self):
        cli = criar_cliente()
        compra = criar_compra_pacote(cli, criar_pacote(procedimento=self.proc, sessoes=2))
        CompraPacote.objects.filter(pk=compra.pk).update(data_expiracao=timezone.localdate() - timedelta(days=2))
        with mock.patch.dict(os.environ, {'CRON_TOKEN': 'segredo-cron'}):
            resp = Client().post('/cron/run/pacote_expirar/', HTTP_X_CRON_TOKEN='segredo-cron',
                                 REMOTE_ADDR='192.0.2.10')
        self.assertEqual(resp.status_code, 200, resp.content)
        h = HistoricoAlteracao.objects.do_registro('compra_pacote', compra.pk).first()
        self.assertEqual(h.operacao, 'UPDATE')
        self.assertEqual(h.colunas_alteradas, ['status'])
        self.assertEqual((h.dados_antes, h.dados_depois), ({'status': 'ATIVO'}, {'status': 'EXPIRADO'}))
        self.assertEqual((h.origem, h.ip_origem, h.usuario_autor_id), ('cron:pacote_expirar', '192.0.2.10', None))
        self.assertEqual(_contexto(), ('', '', ''))

    def test_liveness_nao_toca_o_banco(self):
        with CaptureQueriesContext(connection) as consultas:
            Client().get('/healthz/')  # 200 ou 503 (manifest do front): o que importa e nao ir ao banco
        self.assertFalse([q for q in consultas.captured_queries if 'set_config' in q['sql']])


@skipUnless(PG, 'triggers da L1 so existem no Postgres (0056)')
class TriggersDaTrilhaTests(TestCase):
    def setUp(self):
        self.cli = criar_cliente()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=None)  # Preco criado por cada teste

    def tearDown(self):
        definir_contexto_banco()

    def test_insert_update_delete_e_so_colunas_alteradas(self):
        definir_contexto_banco('', '', 'teste')
        preco = Preco.objects.create(procedimento=self.proc, valor=Decimal('99.00'), descricao='Promo')
        Preco.objects.filter(pk=preco.pk).update(valor=Decimal('89.00'))
        pk = preco.pk
        preco.delete()
        ins, upd, dele = HistoricoAlteracao.objects.filter(tabela='preco', registro_pk=pk).order_by('id')
        self.assertEqual((ins.operacao, ins.dados_antes, ins.colunas_alteradas), ('INSERT', None, []))
        self.assertEqual(ins.dados_depois['descricao'], 'Promo')
        self.assertNotIn('atualizado_em', ins.dados_depois)
        self.assertEqual(upd.colunas_alteradas, ['valor'])
        self.assertEqual((upd.dados_antes, upd.dados_depois), ({'valor': 99.0}, {'valor': 89.0}))
        self.assertEqual((dele.operacao, dele.dados_depois), ('DELETE', None))
        self.assertEqual(dele.dados_antes['valor'], 89.0)
        self.assertEqual({h.origem for h in (ins, upd, dele)}, {'teste'})

    def test_update_so_de_atualizado_em_nao_gera_linha_e_trigger_preenche_atualizado_em(self):
        pront = Prontuario.objects.create(cliente=self.cli, alergias='Latex')
        antes = HistoricoAlteracao.objects.filter(tabela='prontuario').count()
        Prontuario.objects.filter(pk=pront.pk).update(atualizado_em=timezone.now() + timedelta(seconds=5))
        self.assertEqual(HistoricoAlteracao.objects.filter(tabela='prontuario').count(), antes)
        # QuerySet.update() sem atualizado_em: o tg_<t>_atualizado_em preenche (o ORM nao manda)
        marcado = Prontuario.objects.get(pk=pront.pk).atualizado_em
        Prontuario.objects.filter(pk=pront.pk).update(alergias='Latex, iodo')
        self.assertNotEqual(Prontuario.objects.get(pk=pront.pk).atualizado_em, marcado)
        # save() do ORM manda o valor: o banco guarda exatamente o da memoria (trava otimista)
        pront.refresh_from_db()
        pront.alergias = 'Nenhuma'
        pront.save()
        self.assertEqual(Prontuario.objects.get(pk=pront.pk).atualizado_em, pront.atualizado_em)

    def test_senha_hash_redigida(self):
        u = Usuario.objects.create_user(email='redige@x.com', password='senha-1-forte', nome='Recep')
        ins = HistoricoAlteracao.objects.get(tabela='usuario', registro_pk=u.pk, operacao='INSERT')
        self.assertEqual(ins.dados_depois['senha_hash'], '[redigido]')
        u.set_password('senha-2-forte')
        u.save()
        upd = HistoricoAlteracao.objects.filter(tabela='usuario', registro_pk=u.pk, operacao='UPDATE').get()
        self.assertEqual(upd.colunas_alteradas, ['senha_hash'])
        self.assertEqual((upd.dados_antes, upd.dados_depois),
                         ({'senha_hash': '[redigido]'}, {'senha_hash': '[redigido]'}))
        self.assertFalse(HistoricoAlteracao.objects.filter(dados_depois__contains='pbkdf2').exists())

    def test_contexto_invalido_nao_derruba_a_escrita(self):
        definir_contexto_banco('999999999', 'nao-e-ip', 'x' * 80)
        preco = Preco.objects.create(procedimento=self.proc, valor=Decimal('10.00'))
        h = HistoricoAlteracao.objects.get(tabela='preco', registro_pk=preco.pk)
        self.assertIsNone(h.usuario_autor_id)  # usuario inexistente
        self.assertIsNone(h.ip_origem)         # IP invalido
        self.assertEqual(h.origem, 'x' * 60)   # truncado

    def test_cliente_e_tabelas_append_only_fora_da_trilha(self):
        self.cli.nome = 'Outro Nome'
        self.cli.save()
        AnotacaoSessao.objects.create(atendimento=criar_atendimento(self.cli, self.prof, self.proc), texto='ok')
        self.assertFalse(HistoricoAlteracao.objects.filter(tabela__in=['cliente', 'anotacao_sessao']).exists())

    def test_trilha_append_only_e_purga_com_flag(self):
        preco = Preco.objects.create(procedimento=self.proc, valor=Decimal('10.00'))
        h = HistoricoAlteracao.objects.get(tabela='preco', registro_pk=preco.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            HistoricoAlteracao.objects.filter(pk=h.pk).update(origem='forjado')
        with self.assertRaises(IntegrityError), transaction.atomic():
            _sql('DELETE FROM historico_alteracao WHERE id = %s', [h.pk])
        # so o SET NULL do autor passa (usuario excluido)
        _sql('UPDATE historico_alteracao SET usuario_autor_id = NULL WHERE id = %s', [h.pk])

        def _purgar(pk):
            with transaction.atomic():
                _sql("SELECT set_config('app.expurgo_autorizado', 'on', true)")
                _sql('DELETE FROM historico_alteracao WHERE id = %s', [pk])

        # 0059: a flag sozinha nao apaga trilha recente (SQL injetado tambem liga a flag)
        with self.assertRaises(IntegrityError):
            _purgar(h.pk)
        envelhecer_trilha(HistoricoAlteracao, h.pk, timezone.now() - timedelta(days=181))
        _purgar(h.pk)
        self.assertFalse(HistoricoAlteracao.objects.filter(pk=h.pk).exists())

    def test_trilha_do_prontuario_so_sai_com_20_anos(self):
        pront = Prontuario.objects.create(cliente=self.cli, alergias='nenhuma')
        h = HistoricoAlteracao.objects.get(tabela='prontuario', registro_pk=pront.pk)
        envelhecer_trilha(HistoricoAlteracao, h.pk, timezone.now() - timedelta(days=365 * 6))
        with self.assertRaises(IntegrityError), transaction.atomic():
            _sql("SELECT set_config('app.expurgo_autorizado', 'on', true)")
            _sql('DELETE FROM historico_alteracao WHERE id = %s', [h.pk])
        envelhecer_trilha(HistoricoAlteracao, h.pk, timezone.now() - timedelta(days=365 * 20 + 6))
        with transaction.atomic():
            _sql("SELECT set_config('app.expurgo_autorizado', 'on', true)")
            _sql('DELETE FROM historico_alteracao WHERE id = %s', [h.pk])
        self.assertFalse(HistoricoAlteracao.objects.filter(pk=h.pk).exists())

    def test_log_auditoria_append_only(self):
        log = LogAuditoria.objects.create(
            acao='Leu prontuario de Maria Silva', tabela='prontuario', registro_id=self.cli.pk,
            detalhes={'campo': 'alergias'}, ip_origem='10.0.0.1',
        )
        for sql_forjado in (
            "UPDATE log_auditoria SET acao = 'nada aconteceu' WHERE id = %s",
            "UPDATE log_auditoria SET ip_origem = '10.9.9.9' WHERE id = %s",
            "UPDATE log_auditoria SET criado_em = now() - interval '10 years' WHERE id = %s",
            "UPDATE log_auditoria SET detalhes = '{}'::jsonb WHERE id = %s",
            'DELETE FROM log_auditoria WHERE id = %s',
        ):
            with self.subTest(sql=sql_forjado), self.assertRaises(IntegrityError), transaction.atomic():
                _sql(sql_forjado, [log.pk])
        # pseudonimizacao do esquecimento (sp_anonimizar_cliente / LgpdService) passa
        _sql("UPDATE log_auditoria SET acao = replace(acao, 'Maria Silva', '[ANONIMIZADO-1]'), "
             'detalhes = NULL WHERE id = %s', [log.pk])
        log.refresh_from_db()
        self.assertEqual(log.acao, 'Leu prontuario de [ANONIMIZADO-1]')
        self.assertIsNone(log.detalhes)
        # purga so com 180+ dias
        envelhecer_trilha(LogAuditoria, log.pk, timezone.now() - timedelta(days=181))
        _sql('DELETE FROM log_auditoria WHERE id = %s', [log.pk])
        self.assertFalse(LogAuditoria.objects.filter(pk=log.pk).exists())

    def test_excluir_usuario_anula_o_autor_do_log_auditoria(self):
        u = Usuario.objects.create_user(email='temp-log@x.com', password='senha-1-forte', nome='Temp')
        log = LogAuditoria.objects.create(usuario=u, acao='Exportou relatorio')
        u.delete()
        log.refresh_from_db()
        self.assertIsNone(log.usuario_id)
        self.assertTrue(log.usuario_nome)          # o snapshot do autor fica

    def test_excluir_usuario_anula_o_autor_da_trilha(self):
        u = Usuario.objects.create_user(email='temp@x.com', password='senha-1-forte', nome='Temp')
        definir_contexto_banco(u.pk, '10.1.1.1', 'web')
        preco = Preco.objects.create(procedimento=self.proc, valor=Decimal('10.00'))
        h = HistoricoAlteracao.objects.get(tabela='preco', registro_pk=preco.pk)
        self.assertEqual(h.usuario_autor_id, u.pk)
        definir_contexto_banco()
        pk = u.pk
        u.delete()  # SET NULL do autor: a unica mutacao que tg_historico_alteracao_imutavel aceita
        h.refresh_from_db()
        self.assertIsNone(h.usuario_autor_id)
        self.assertTrue(HistoricoAlteracao.objects.filter(tabela='usuario', registro_pk=pk, operacao='DELETE').exists())

    def test_anotacao_sessao_imutavel_no_banco(self):
        nota = AnotacaoSessao.objects.create(
            atendimento=criar_atendimento(self.cli, self.prof, self.proc), texto='Sem intercorrencias',
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            _sql("UPDATE anotacao_sessao SET texto = 'forjado' WHERE id = %s", [nota.pk])
        with self.assertRaises(IntegrityError), transaction.atomic():
            _sql('DELETE FROM anotacao_sessao WHERE id = %s', [nota.pk])


@skipUnless(PG, 'GUCs app.* so no Postgres')
class ContextoDaSessaoTests(TestCase):
    def tearDown(self):
        definir_contexto_banco()

    def test_middleware_define_durante_e_limpa_mesmo_com_erro(self):
        visto = {}

        def view(request):
            visto['durante'] = _contexto()
            raise RuntimeError('quebrou')

        request = RequestFactory().get('/painel/', REMOTE_ADDR='203.0.113.50')
        request.user = Usuario.objects.create_user(email='mw@x.com', password='senha-1-forte')
        with self.assertRaises(RuntimeError):
            ContextoBancoMiddleware(view)(request)
        self.assertEqual(visto['durante'], (str(request.user.pk), '203.0.113.50', 'web'))
        self.assertEqual(_contexto(), ('', '', ''))

    def test_anonimo_e_ip_desconhecido_viram_vazio(self):
        from django.contrib.auth.models import AnonymousUser

        visto = {}

        def view(request):
            visto['durante'] = _contexto()
            return HttpResponse('ok')

        request = RequestFactory().get('/', REMOTE_ADDR='')
        request.user = AnonymousUser()
        ContextoBancoMiddleware(view)(request)
        self.assertEqual(visto['durante'], ('', '', 'web'))

    def test_conexao_nova_nasce_limpa(self):
        definir_contexto_banco(7, '10.0.0.1', 'web')
        limpar_contexto_conexao_nova(None, connection)
        self.assertEqual(_contexto(), ('', '', ''))

    def test_comando_rodar_job_grava_origem_e_limpa(self):
        from django.core.management import call_command

        visto = {}
        with mock.patch.dict('aranha_estetica.jobs.JOB_MAP', {'x': lambda: visto.setdefault('c', _contexto())}):
            call_command('rodar_job', 'x', stdout=StringIO())
        self.assertEqual(visto['c'], ('', '', 'comando:x'))
        self.assertEqual(_contexto(), ('', '', ''))
