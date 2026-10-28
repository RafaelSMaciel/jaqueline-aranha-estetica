"""Regressao das data migrations da remodelagem (0029, 0034-0037, 0042-0053).

Roda a migration real sobre dados "sujos" plausiveis do banco legado (SQLite:
as partes PG-only — CHECK regex, EXCLUDE, trigger, collation — sao validadas
no ensaio em Postgres; aqui fica a logica de dados, que e portavel).

Tag ``migracoes``: estes testes descem e sobem a cadeia de migrations dezenas de
vezes (~3/4 do tempo da suite). A suite padrao roda TUDO; no dia a dia,
``manage.py test aranha_estetica --exclude-tag migracoes`` e o atalho (o CI roda
a tag num job separado, em paralelo).
"""
import io
from contextlib import redirect_stderr
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from types import SimpleNamespace

from django.contrib.auth.hashers import make_password
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, tag
from django.utils import timezone

APP = 'aranha_estetica'


@tag('migracoes')
class _MigracaoBase(TransactionTestCase):
    serialized_rollback = True
    migrate_from = None
    migrate_to = None

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([(APP, self.migrate_from)])
        antes = executor.loader.project_state([(APP, self.migrate_from)]).apps
        self.preparar(antes)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([(APP, self.migrate_to)])
        self.apps = executor.loader.project_state([(APP, self.migrate_to)]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def preparar(self, apps):
        raise NotImplementedError


@tag('migracoes')
class PapelUsuario0029Tests(_MigracaoBase):
    """0029: usuario ligado a Profissional nunca cai em RECEPCAO (sem telas)."""
    migrate_from = '0028_remodelagem_fase1b_otp_unico'
    migrate_to = '0029_remodelagem_fase1c_papel_usuario'

    def preparar(self, apps):
        Usuario = apps.get_model(APP, 'Usuario')
        Perfil = apps.get_model(APP, 'Perfil')
        Profissional = apps.get_model(APP, 'Profissional')
        perfis = {nome: Perfil.objects.create(nome=nome)
                  for nome in ('Administrador', 'Profissional', 'Recepcionista', 'Gerente')}
        profs = iter([Profissional.objects.create(nome=f'Prof {i}') for i in range(5)])

        def usuario(email, perfil=None, vinculado=False):
            return Usuario.objects.create(
                email=email, nome=email.split('@')[0], password='!',
                perfil=perfis.get(perfil), profissional=next(profs) if vinculado else None,
            ).pk

        self.ids = {
            'admin_vinculado': usuario('admin@x.com', 'Administrador', vinculado=True),
            'vinculado_sem_perfil': usuario('semperfil@x.com', vinculado=True),
            'vinculado_desconhecido': usuario('gerente@x.com', 'Gerente', vinculado=True),
            'vinculado_recepcao': usuario('recprof@x.com', 'Recepcionista', vinculado=True),
            'profissional': usuario('prof@x.com', 'Profissional', vinculado=True),
            'recepcao': usuario('recepcao@x.com', 'Recepcionista'),
            'sem_perfil': usuario('orfao@x.com'),
        }

    def test_vinculado_vira_profissional_e_recepcao_fica_no_log(self):
        Usuario = self.apps.get_model(APP, 'Usuario')
        papel = {k: Usuario.objects.get(pk=pk).papel for k, pk in self.ids.items()}
        self.assertEqual(papel, {
            'admin_vinculado': 'ADMIN',
            'vinculado_sem_perfil': 'PROFISSIONAL',
            'vinculado_desconhecido': 'PROFISSIONAL',
            'vinculado_recepcao': 'PROFISSIONAL',
            'profissional': 'PROFISSIONAL',
            'recepcao': 'RECEPCAO',
            'sem_perfil': 'RECEPCAO',
        })
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        logs = LogAuditoria.objects.filter(acao__startswith='migration 0029')
        self.assertEqual(
            {log.id_registro_afetado: log.detalhes for log in logs},
            {self.ids['recepcao']: {'perfil': 'Recepcionista', 'mapeado': True},
             self.ids['sem_perfil']: {'perfil': None, 'mapeado': False}},
        )
        self.assertTrue(all(log.tabela_afetada == 'usuario' for log in logs))

    def tearDown(self):
        # o papel RECEPCAO saiu na 0066, que aborta se ainda houver um: o legado do teste sai antes
        self.apps.get_model(APP, 'Usuario').objects.filter(papel='RECEPCAO').delete()
        super().tearDown()


@tag('migracoes')
class Remodelagem0034a0037Tests(_MigracaoBase):
    migrate_from = '0033_remodelagem_fase2c_cliente_nome'
    migrate_to = '0037_remodelagem_fase6_aceite_termo'

    def preparar(self, apps):
        Cliente = apps.get_model(APP, 'Cliente')
        Profissional = apps.get_model(APP, 'Profissional')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Atendimento = apps.get_model(APP, 'Atendimento')
        VersaoTermo = apps.get_model(APP, 'VersaoTermo')
        Preco = apps.get_model(APP, 'Preco')
        Promocao = apps.get_model(APP, 'Promocao')
        ListaEspera = apps.get_model(APP, 'ListaEspera')
        Pergunta = apps.get_model(APP, 'ProntuarioPergunta')
        Resposta = apps.get_model(APP, 'ProntuarioResposta')
        Prontuario = apps.get_model(APP, 'Prontuario')
        AceitePrivacidade = apps.get_model(APP, 'AceitePrivacidade')
        Assinatura = apps.get_model(APP, 'AssinaturaTermoProcedimento')

        agora = timezone.now()
        self.antigo = (agora - timedelta(days=400)).replace(microsecond=0)
        prof = Profissional.objects.create(nome='Jaqueline Aranha')
        proc = Procedimento.objects.create(nome='Limpeza de Pele', duracao_minutos=60)

        # cliente com historico (mais antigo) x duplicata nova sem historico
        hist = Cliente.objects.create(nome='João da Silva', telefone='(11) 99999-9999',
                                      cpf='529.982.247-25', email='Joao@X.com')
        Cliente.objects.filter(pk=hist.pk).update(criado_em=self.antigo)
        novo = Cliente.objects.create(nome='Joao Silva', telefone='11999999999',
                                      cpf='52998224725', email=' joao@x.com ')
        ddi = Cliente.objects.create(nome='Carla DDI', telefone='+55 17 98765-4321')
        curto = Cliente.objects.create(nome='Sem DDD', telefone='99999-9999', cpf='123')
        zero = Cliente.objects.create(nome='Zero Tronco', telefone='017 3333-0000')
        self.ids = {'hist': hist.pk, 'novo': novo.pk, 'ddi': ddi.pk, 'curto': curto.pk, 'zero': zero.pk}

        def atend(cliente, inicio, minutos=60, **kw):
            return Atendimento.objects.create(
                cliente=cliente, profissional=prof, procedimento=proc,
                data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=minutos), **kw)

        futuro = (agora + timedelta(days=10)).replace(hour=14, minute=0, second=0, microsecond=0)
        passado = (agora - timedelta(days=10)).replace(hour=14, minute=0, second=0, microsecond=0)
        origem = atend(hist, passado - timedelta(days=5), status='REALIZADO')
        self.agendado = atend(hist, futuro, status='AGENDADO')
        # retorno PENDENTE sugerido pelo sistema em cima de horario ocupado
        self.retorno = atend(ddi, futuro + timedelta(minutes=30), status='PENDENTE',
                             eh_retorno=True, atendimento_origem=origem, valor_cobrado=0)
        # solicitacao PENDENTE vencida sobreposta a um AGENDADO antigo
        self.antigo_ag = atend(zero, passado, status='AGENDADO')
        self.pend_vencido = atend(curto, passado + timedelta(minutes=15), status='PENDENTE')

        # 0034: termo/preco/promocao/lista duplicados ou fora do CHECK
        hoje = timezone.localdate()
        self.termo_v1 = VersaoTermo.objects.create(tipo='LGPD', titulo='v1', conteudo='.', versao='1',
                                                   vigente_desde=hoje - timedelta(days=30), ativa=True)
        self.termo_v2 = VersaoTermo.objects.create(tipo='LGPD', titulo='v2', conteudo='.', versao='2',
                                                   vigente_desde=hoje, ativa=True)
        Preco.objects.create(procedimento=proc, valor=Decimal('100'), vigente_desde=hoje)
        self.preco_novo = Preco.objects.create(procedimento=proc, valor=Decimal('130'), vigente_desde=hoje)
        self.promo_xor = Promocao.objects.create(procedimento=proc, nome='Combo', desconto_percentual=Decimal('10'),
                                                 preco_promocional=Decimal('99'), data_inicio=hoje,
                                                 data_fim=hoje + timedelta(days=5))
        self.promo_150 = Promocao.objects.create(procedimento=proc, nome='Erro', desconto_percentual=Decimal('150'),
                                                 data_inicio=hoje, data_fim=hoje + timedelta(days=5), ativa=True)
        self.promo_neg = Promocao.objects.create(procedimento=proc, nome='Neg', desconto_percentual=Decimal('-10'),
                                                 data_inicio=hoje, data_fim=hoje + timedelta(days=5), ativa=True)
        self.promo_100 = Promocao.objects.create(procedimento=proc, nome='Cortesia', desconto_percentual=Decimal('100'),
                                                 data_inicio=hoje, data_fim=hoje + timedelta(days=5), ativa=True)
        for _ in range(2):
            ListaEspera.objects.create(cliente=novo, procedimento=proc, data_desejada=hoje + timedelta(days=3))

        # 0036: EAV com 2+ respostas no mesmo prontuario + pergunta desativada
        p1 = Pergunta.objects.create(texto='Fumante?', tipo_resposta='BOOLEAN', ativa=True)
        p2 = Pergunta.objects.create(texto='Alergia a cosméticos?', tipo_resposta='BOOLEAN', ativa=True)
        p3 = Pergunta.objects.create(texto='Pergunta antiga', tipo_resposta='TEXTO', ativa=False)
        pront = Prontuario.objects.create(cliente=hist)
        self.pront_id = pront.pk
        Resposta.objects.create(prontuario=pront, pergunta=p1, resposta_boolean=True)
        Resposta.objects.create(prontuario=pront, pergunta=p2, resposta_boolean=False)
        Resposta.objects.create(prontuario=pront, pergunta=p3, resposta_texto='resposta historica')

        # 0037: aceite antigo com IP invalido + assinatura com IP valido
        ap = AceitePrivacidade.objects.create(cliente=hist, versao_termo=self.termo_v1, ip='unknown')
        AceitePrivacidade.objects.filter(pk=ap.pk).update(criado_em=self.antigo)
        vp = VersaoTermo.objects.create(tipo='PROCEDIMENTO', procedimento=proc, titulo='Termo', conteudo='.',
                                        versao='1', vigente_desde=hoje, ativa=True)
        self.termo_proc = vp
        Assinatura.objects.create(cliente=ddi, versao_termo=vp, atendimento=self.agendado, ip='200.10.20.30')

    def test_dados_migrados_sem_perda(self):
        Cliente = self.apps.get_model(APP, 'Cliente')
        Atendimento = self.apps.get_model(APP, 'Atendimento')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        c = {k: Cliente.objects.get(pk=pk) for k, pk in self.ids.items()}

        # 0034 telefone: DDI/zero removidos, invalido vira NULL
        self.assertEqual(c['ddi'].telefone, '17987654321')
        self.assertEqual(c['zero'].telefone, '1733330000')
        self.assertIsNone(c['curto'].telefone)
        self.assertIsNone(c['curto'].cpf)
        # telefone sem DDD: o original (digitos) fica no log p/ a equipe corrigir
        log_curto = LogAuditoria.objects.get(registro_id=self.ids['curto'], acao__contains='telefone invalido')
        self.assertEqual(log_curto.detalhes['telefone_original'], '999999999')
        # dedup: quem tem historico mantem telefone/cpf/email; ninguem soft-deletado
        self.assertEqual(c['hist'].telefone, '11999999999')
        self.assertEqual(c['hist'].cpf, '52998224725')
        self.assertEqual(c['hist'].email, 'Joao@X.com')
        self.assertIsNone(c['novo'].telefone)
        self.assertIsNone(c['novo'].cpf)
        self.assertIsNone(c['novo'].email)
        self.assertFalse(Cliente.objects.filter(deletado_em__isnull=False).exists())
        self.assertTrue(all(x.ativo for x in c.values()))
        log = LogAuditoria.objects.filter(registro_id=self.ids['novo'], acao__contains='telefone duplicado').get()
        self.assertEqual(log.detalhes['duplicada_de'], self.ids['hist'])
        self.assertNotIn('99999', str(log.detalhes))  # PII mascarada

        # 0034 demais tabelas
        VersaoTermo = self.apps.get_model(APP, 'VersaoTermo')
        self.assertFalse(VersaoTermo.objects.get(pk=self.termo_v1.pk).ativa)
        self.assertTrue(VersaoTermo.objects.get(pk=self.termo_v2.pk).ativa)
        Preco = self.apps.get_model(APP, 'Preco')
        self.assertEqual(list(Preco.objects.values_list('pk', flat=True)), [self.preco_novo.pk])
        Promocao = self.apps.get_model(APP, 'Promocao')
        xor = Promocao.objects.get(pk=self.promo_xor.pk)
        self.assertEqual(xor.desconto_percentual, Decimal('10'))
        self.assertIsNone(xor.preco_promocional)
        # fora da faixa: travada no CHECK e DESATIVADA (150% = servico de graca)
        p150 = Promocao.objects.get(pk=self.promo_150.pk)
        self.assertEqual(p150.desconto_percentual, Decimal('100'))
        self.assertFalse(p150.ativa)
        self.assertFalse(Promocao.objects.get(pk=self.promo_neg.pk).ativa)
        self.assertTrue(Promocao.objects.get(pk=self.promo_100.pk).ativa)  # 100% digitado e legitimo
        self.assertTrue(xor.ativa)
        log150 = LogAuditoria.objects.get(tabela='promocao', registro_id=self.promo_150.pk)
        self.assertEqual(log150.detalhes['desconto_de'], '150.00')
        self.assertTrue(log150.detalhes['desativada'])
        logxor = LogAuditoria.objects.get(tabela='promocao', registro_id=self.promo_xor.pk)
        self.assertEqual(logxor.detalhes['preco_de'], '99.00')
        self.assertFalse(logxor.detalhes['desativada'])
        self.assertFalse(LogAuditoria.objects.filter(tabela='promocao', registro_id=self.promo_100.pk).exists())
        ListaEspera = self.apps.get_model(APP, 'ListaEspera')
        self.assertEqual(ListaEspera.objects.filter(notificado=False).count(), 1)

        # 0035: so PENDENTE vencido / retorno sugerido sao cancelados
        self.assertEqual(Atendimento.objects.get(pk=self.retorno.pk).status, 'CANCELADO')
        self.assertEqual(Atendimento.objects.get(pk=self.pend_vencido.pk).status, 'CANCELADO')
        self.assertEqual(Atendimento.objects.get(pk=self.agendado.pk).status, 'AGENDADO')
        self.assertEqual(Atendimento.objects.get(pk=self.antigo_ag.pk).status, 'AGENDADO')

        # 0036: todas as respostas agregadas, inclusive de pergunta desativada
        Prontuario = self.apps.get_model(APP, 'Prontuario')
        Configuracao = self.apps.get_model(APP, 'Configuracao')
        extras = Prontuario.objects.get(pk=self.pront_id).respostas_extras
        self.assertEqual(extras, {
            'fumante': True, 'alergia_a_cosmeticos': False, 'pergunta_antiga': 'resposta historica',
        })
        schema = Configuracao.objects.get(chave='prontuario_perguntas').valor
        self.assertIn('fumante', schema)
        self.assertNotIn('pergunta_antiga', schema)

        # 0037: data legal do aceite preservada; IP invalido vira NULL
        AceiteTermo = self.apps.get_model(APP, 'AceiteTermo')
        lgpd = AceiteTermo.objects.get(cliente_id=self.ids['hist'], versao_termo_id=self.termo_v1.pk)
        self.assertEqual(lgpd.criado_em, self.antigo)
        self.assertIsNone(lgpd.ip)
        assinatura = AceiteTermo.objects.get(cliente_id=self.ids['ddi'], versao_termo_id=self.termo_proc.pk)
        self.assertEqual(assinatura.ip, '200.10.20.30')
        self.assertEqual(assinatura.atendimento_id, self.agendado.pk)


@tag('migracoes')
class ContasDemoERetornoDuplicadoTests(_MigracaoBase):
    migrate_from = '0041_on_delete_protect_indice_email_upper'
    migrate_to = '0043_retorno_unico_checks_jsonb'

    def setUp(self):
        # 0042 avisa em stderr quando o painel fica sem ADMIN (lockout silencioso)
        self.stderr = io.StringIO()
        env_sem_admin = {'ADMIN_EMAIL': '', 'ADMIN_PASSWORD': ''}
        with mock.patch.dict('os.environ', env_sem_admin), redirect_stderr(self.stderr):
            super().setUp()

    def preparar(self, apps):
        Usuario = apps.get_model(APP, 'Usuario')
        self.demo = Usuario.objects.create(email='admin@shivazen.com', nome='Admin',
                                           password=make_password('admin123'), papel='ADMIN')
        self.trocada = Usuario.objects.create(email='ana@shivazen.com', nome='Ana',
                                              password=make_password('senha-trocada-forte'),
                                              papel='PROFISSIONAL')
        # 2FA cadastrado por quem usou a senha publica (main aceitava) x 2FA legitimo
        from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
        from django_otp.plugins.otp_totp.models import TOTPDevice
        TOTPDevice.objects.create(user_id=self.demo.pk, name='atacante', confirmed=True)
        sd = StaticDevice.objects.create(user_id=self.demo.pk, name='atk', confirmed=True)
        StaticToken.objects.create(device=sd, token='atk00001')
        self.totp_legitimo = TOTPDevice.objects.create(user_id=self.trocada.pk, name='ana', confirmed=True)

        Cliente = apps.get_model(APP, 'Cliente')
        Profissional = apps.get_model(APP, 'Profissional')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Atendimento = apps.get_model(APP, 'Atendimento')
        cli = Cliente.objects.create(nome='Cliente', telefone='11911112222')
        prof = Profissional.objects.create(nome='Prof')
        proc = Procedimento.objects.create(nome='Botox 1 região', duracao_minutos=30)
        base = timezone.now() + timedelta(days=20)

        def atend(delta_h, **kw):
            ini = base + timedelta(hours=delta_h)
            return Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc,
                                              data_hora_inicio=ini, data_hora_fim=ini + timedelta(minutes=30), **kw)

        origem = atend(-500, status='REALIZADO')
        self.ret_ok = atend(0, status='AGENDADO', eh_retorno=True, atendimento_origem=origem, valor_cobrado=0)
        self.ret_dup = atend(2, status='PENDENTE', eh_retorno=True, atendimento_origem=origem, valor_cobrado=0)

    def test_desativa_so_senha_padrao_e_cancela_retorno_duplicado(self):
        Usuario = self.apps.get_model(APP, 'Usuario')
        demo = Usuario.objects.get(pk=self.demo.pk)
        self.assertFalse(demo.ativo)
        self.assertTrue(demo.password.startswith('!'))  # senha inutilizavel
        self.assertTrue(Usuario.objects.get(pk=self.trocada.pk).ativo)
        self.assertIn('painel ficou sem ADMIN ativo', self.stderr.getvalue())
        self.assertIn('ADMIN_EMAIL/ADMIN_PASSWORD', self.stderr.getvalue())

        # 2FA da conta demo nao sobrevive (reativar exige cadastrar de novo)
        from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
        from django_otp.plugins.otp_totp.models import TOTPDevice
        self.assertFalse(TOTPDevice.objects.filter(user_id=self.demo.pk).exists())
        self.assertFalse(StaticDevice.objects.filter(user_id=self.demo.pk).exists())
        self.assertFalse(StaticToken.objects.filter(token='atk00001').exists())
        self.assertTrue(TOTPDevice.objects.filter(pk=self.totp_legitimo.pk).exists())
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        log = LogAuditoria.objects.get(tabela='usuario', registro_id=self.demo.pk)
        self.assertEqual(log.detalhes, {'dispositivos_2fa_removidos': 2})

        Atendimento = self.apps.get_model(APP, 'Atendimento')
        self.assertEqual(Atendimento.objects.get(pk=self.ret_dup.pk).status, 'CANCELADO')
        self.assertEqual(Atendimento.objects.get(pk=self.ret_ok.pk).status, 'AGENDADO')


@tag('migracoes')
class AvisoContaDemoSoNoCommitTests(TestCase):
    """0042: com rollback (migrate_atomico falhou depois) o aviso nao sai."""

    def _rodar(self, falhar):
        import importlib

        from django.apps import apps as global_apps

        mod = importlib.import_module('aranha_estetica.migrations.0042_desativar_contas_demo')
        schema_editor = SimpleNamespace(connection=connection)
        err = io.StringIO()
        with mock.patch.dict('os.environ', {'ADMIN_EMAIL': '', 'ADMIN_PASSWORD': ''}), redirect_stderr(err):
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        mod.desativar_contas_demo(global_apps, schema_editor)
                        if falhar:
                            raise RuntimeError('migration posterior falhou')
                except RuntimeError:
                    pass
        return err.getvalue()

    def test_rollback_nao_avisa_e_commit_avisa(self):
        from aranha_estetica.models import Usuario
        demo = Usuario.objects.create(email='admin@shivazen.com', nome='A',
                                      password=make_password('admin123'), papel='ADMIN')
        self.assertEqual(self._rodar(falhar=True), '')
        demo.refresh_from_db()
        self.assertTrue(demo.ativo)  # desfeito: a conta segue como estava
        self.assertIn('conta demo desativada', self._rodar(falhar=False))


@tag('migracoes')
class ProvaAceiteEAutoria0044e0045Tests(_MigracaoBase):
    migrate_from = '0043_retorno_unico_checks_jsonb'
    migrate_to = '0045_dados_termo_lgpd_autoria'

    def preparar(self, apps):
        # o banco de teste ja passou pela 0045: sem termo LGPD, como o de prod
        apps.get_model(APP, 'VersaoTermo').objects.filter(tipo='LGPD').delete()
        Usuario = apps.get_model(APP, 'Usuario')
        Cliente = apps.get_model(APP, 'Cliente')
        Profissional = apps.get_model(APP, 'Profissional')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Atendimento = apps.get_model(APP, 'Atendimento')
        AnotacaoSessao = apps.get_model(APP, 'AnotacaoSessao')
        Notificacao = apps.get_model(APP, 'Notificacao')
        RegraComissao = apps.get_model(APP, 'RegraComissao')

        autora = Usuario.objects.create(email='dra@x.com', nome='Dra. Clara', password='!', papel='PROFISSIONAL')
        cli = Cliente.objects.create(nome='Cliente', telefone='11911112222')
        prof = Profissional.objects.create(nome='Prof')
        proc = Procedimento.objects.create(nome='Peeling', duracao_minutos=30)
        ini = timezone.now() + timedelta(days=5)
        at = Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc, status='AGENDADO',
                                        data_hora_inicio=ini, data_hora_fim=ini + timedelta(minutes=30))
        self.nota = AnotacaoSessao.objects.create(atendimento=at, autor=autora, texto='Nota')
        self.nota_sistema = AnotacaoSessao.objects.create(atendimento=at, autor=None, texto='Automatica')
        self.n_termo = Notificacao.objects.create(atendimento=at, tipo='LEMBRETE', canal='EMAIL', token='tk-termo')
        self.n_whats = Notificacao.objects.create(atendimento=at, tipo='LEMBRETE', canal='WHATSAPP', token='tk-d1')
        # antes da 0044 nao havia CHECK de 0..100
        self.regra = RegraComissao.objects.create(percentual=Decimal('150'), ativo=True)

    def test_autoria_tokens_de_termo_percentual_e_termo_lgpd(self):
        from aranha_estetica.constants import TERMO_LGPD_CONTEUDO, TERMO_LGPD_VERSAO

        AnotacaoSessao = self.apps.get_model(APP, 'AnotacaoSessao')
        self.assertEqual(AnotacaoSessao.objects.get(pk=self.nota.pk).autor_nome, 'Dra. Clara')
        self.assertEqual(AnotacaoSessao.objects.get(pk=self.nota_sistema.pk).autor_nome, '')

        Notificacao = self.apps.get_model(APP, 'Notificacao')
        self.assertEqual(Notificacao.objects.get(pk=self.n_termo.pk).tipo, 'TERMO')
        self.assertEqual(Notificacao.objects.get(pk=self.n_whats.pk).tipo, 'LEMBRETE')

        RegraComissao = self.apps.get_model(APP, 'RegraComissao')
        self.assertEqual(RegraComissao.objects.get(pk=self.regra.pk).percentual, Decimal('100'))
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        self.assertTrue(LogAuditoria.objects.filter(tabela='regra_comissao', registro_id=self.regra.pk).exists())

        # banco em operacao sem termo LGPD ganha a v1.0 (resumo da politica)
        VersaoTermo = self.apps.get_model(APP, 'VersaoTermo')
        termo = VersaoTermo.objects.get(tipo='LGPD', procedimento__isnull=True, ativa=True)
        self.assertEqual(termo.versao, TERMO_LGPD_VERSAO)
        self.assertEqual(termo.conteudo, TERMO_LGPD_CONTEUDO)
        self.assertIn('/politica-de-privacidade/', termo.conteudo)
        # banco que ja opera: a publicacao pela migration fica na trilha
        self.assertTrue(LogAuditoria.objects.filter(tabela='versao_termo', registro_id=termo.pk).exists())


@tag('migracoes')
class TermoLgpdBancoNovo0045Tests(_MigracaoBase):
    migrate_from = '0044_prova_aceite_autoria_anotacao_ajustes'
    migrate_to = '0045_dados_termo_lgpd_autoria'

    def preparar(self, apps):
        # instalacao limpa: sem cliente e sem termo LGPD (o banco de teste ja
        # passou pela 0045 uma vez; a reversa dela nao apaga o termo)
        apps.get_model(APP, 'VersaoTermo').objects.filter(tipo='LGPD').delete()
        self.assertFalse(apps.get_model(APP, 'Cliente').objects.exists())

    def test_banco_sem_clientes_recebe_termo_lgpd_v1(self):
        """Contrato 3: sem termo o booking recusa — banco novo ja nasce com a v1.0."""
        from aranha_estetica.constants import TERMO_LGPD_CONTEUDO, TERMO_LGPD_VERSAO

        self.assertFalse(self.apps.get_model(APP, 'Cliente').objects.exists())
        VersaoTermo = self.apps.get_model(APP, 'VersaoTermo')
        termo = VersaoTermo.objects.get(tipo='LGPD', procedimento__isnull=True, ativa=True)
        self.assertEqual((termo.versao, termo.conteudo), (TERMO_LGPD_VERSAO, TERMO_LGPD_CONTEUDO))
        # instalacao limpa: nada operando p/ a trilha explicar
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        self.assertFalse(LogAuditoria.objects.filter(tabela='versao_termo', registro_id=termo.pk).exists())


@tag('migracoes')
class HistoricoAutoriaEFichasLegado0046Tests(_MigracaoBase):
    migrate_from = '0045_dados_termo_lgpd_autoria'
    migrate_to = '0046_prontuario_versao_autoria_log_prova_aceite'

    def preparar(self, apps):
        Usuario = apps.get_model(APP, 'Usuario')
        LogAuditoria = apps.get_model(APP, 'LogAuditoria')
        Cliente = apps.get_model(APP, 'Cliente')
        Profissional = apps.get_model(APP, 'Profissional')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Atendimento = apps.get_model(APP, 'Atendimento')
        Formulario = apps.get_model(APP, 'FormularioAnamnese')
        Resposta = apps.get_model(APP, 'RespostaAnamnese')

        dona = Usuario.objects.create(email='dona@clinica.com', nome='Dona Jaqueline', password='!', papel='ADMIN')
        self.log_dona = LogAuditoria.objects.create(usuario=dona, acao='Acessou prontuario', tabela='prontuario')
        self.log_sistema = LogAuditoria.objects.create(acao='Purga automatica')

        cli = Cliente.objects.create(nome='Cliente', telefone='11911112222')
        self.cliente_pk = cli.pk
        prof = Profissional.objects.create(nome='Prof')
        proc = Procedimento.objects.create(nome='Peeling', duracao_minutos=30)
        ini = timezone.now() + timedelta(days=5)
        at = Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc, status='AGENDADO',
                                        data_hora_inicio=ini, data_hora_fim=ini + timedelta(minutes=30))
        anamnese = Formulario.objects.create(nome='Anamnese', tipo='ANAMNESE', schema_json=[])
        pesquisa = Formulario.objects.create(nome='Pesquisa', tipo='PESQUISA', schema_json=[])
        # booking legado: ficha respondida no wizard, gravada sem respondida_em
        self.legado = Resposta.objects.create(formulario=anamnese, cliente=cli, atendimento=at,
                                              respostas_json={'alergias': 'Dipirona'})
        # convite pendente (pesquisa vazia) e ficha avulsa sem atendimento: ficam como estao
        self.pendente = Resposta.objects.create(formulario=pesquisa, cliente=cli, atendimento=at, respostas_json={})
        self.avulsa = Resposta.objects.create(formulario=anamnese, cliente=cli, respostas_json={'x': 'y'})

    def test_autor_do_log_e_fichas_do_booking_legado(self):
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        self.assertEqual(LogAuditoria.objects.get(pk=self.log_dona.pk).usuario_nome,
                         'Dona Jaqueline <dona@clinica.com>')
        self.assertEqual(LogAuditoria.objects.get(pk=self.log_sistema.pk).usuario_nome, '')

        Resposta = self.apps.get_model(APP, 'RespostaAnamnese')
        legado = Resposta.objects.get(pk=self.legado.pk)
        self.assertEqual(legado.respondida_em, legado.criado_em)
        self.assertIsNone(Resposta.objects.get(pk=self.pendente.pk).respondida_em)
        self.assertIsNone(Resposta.objects.get(pk=self.avulsa.pk).respondida_em)

        # a ficha legada passa a gerar alerta de saude (utils/saude) — codigo atual = esquema
        # final (colunas renomeadas na 0054): sobe a cadeia antes de usar os models reais
        _subir_ate_o_fim()
        from aranha_estetica.models import Cliente
        from aranha_estetica.utils.saude import alertas_saude
        alertas = alertas_saude(Cliente.objects.get(pk=self.cliente_pk))
        self.assertIn('Dipirona', [a['valor'] for a in alertas])


@tag('migracoes')
class TermoSaudeReembolsoPromocao0047Tests(_MigracaoBase):
    migrate_from = '0046_prontuario_versao_autoria_log_prova_aceite'
    migrate_to = '0047_termo_saude_reembolso_promocao_geral'

    def preparar(self, apps):
        Promocao = apps.get_model(APP, 'Promocao')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Cliente = apps.get_model(APP, 'Cliente')
        Pacote = apps.get_model(APP, 'Pacote')
        CompraPacote = apps.get_model(APP, 'CompraPacote')
        # a reversa da 0047 apagou o SAUDE do banco de teste (sem aceite)
        self.assertFalse(apps.get_model(APP, 'VersaoTermo').objects.filter(tipo='SAUDE').exists())

        hoje = timezone.localdate()
        datas = {'data_inicio': hoje, 'data_fim': hoje + timedelta(days=10)}
        proc = Procedimento.objects.create(nome='Peeling', duracao_minutos=30)
        # geral de preco fixo: ignorada no preco (utils.precos), mas ativa no site
        self.geral_fixa = Promocao.objects.create(nome='Tudo por 49', preco_promocional=Decimal('49.00'),
                                                  ativa=True, **datas)
        self.geral_pct = Promocao.objects.create(nome='Semana', desconto_percentual=Decimal('10'), **datas)
        self.proc_fixa = Promocao.objects.create(nome='Peeling por 99', procedimento=proc,
                                                 preco_promocional=Decimal('99.00'), **datas)
        cli = Cliente.objects.create(nome='Cliente', telefone='11911112222')
        pacote = Pacote.objects.create(nome='Glow', preco_total=Decimal('600'))
        self.compra = CompraPacote.objects.create(cliente=cli, pacote=pacote, valor_pago=Decimal('600'),
                                                  status='CANCELADO')

    def test_saude_v1_reembolso_zero_e_promo_geral_saneada(self):
        from aranha_estetica.models.termos import TEXTO_CONSENTIMENTO_SAUDE

        VersaoTermo = self.apps.get_model(APP, 'VersaoTermo')
        saude = VersaoTermo.objects.get(tipo='SAUDE', ativa=True)
        self.assertEqual((saude.versao, saude.conteudo), ('1.0', TEXTO_CONSENTIMENTO_SAUDE))
        self.assertIsNone(saude.procedimento_id)
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        self.assertTrue(LogAuditoria.objects.filter(tabela='versao_termo', registro_id=saude.pk).exists())

        CompraPacote = self.apps.get_model(APP, 'CompraPacote')
        self.assertEqual(CompraPacote.objects.get(pk=self.compra.pk).valor_reembolsado, Decimal('0'))

        Promocao = self.apps.get_model(APP, 'Promocao')
        fixa = Promocao.objects.get(pk=self.geral_fixa.pk)
        self.assertIsNone(fixa.preco_promocional)
        self.assertFalse(fixa.ativa)
        log = LogAuditoria.objects.get(tabela='promocao', registro_id=self.geral_fixa.pk)
        self.assertEqual(log.detalhes, {'preco_de': '49.00', 'desativada': True})
        self.assertTrue(Promocao.objects.get(pk=self.geral_pct.pk).ativa)
        self.assertEqual(Promocao.objects.get(pk=self.proc_fixa.pk).preco_promocional, Decimal('99.00'))
        self.assertEqual(LogAuditoria.objects.filter(tabela='promocao').count(), 1)


@tag('migracoes')
class ReversaTermoSaude0047Tests(TestCase):
    """Reversa (dev) da 0047: apaga o termo SAUDE sem aceite; com aceite (prova) recusa."""

    def _reverter(self):
        import importlib

        from django.apps import apps as global_apps

        mod = importlib.import_module('aranha_estetica.migrations.0047_termo_saude_reembolso_promocao_geral')

        def executar(sql, params=()):
            with connection.cursor() as cursor:
                cursor.execute(sql, params)

        mod.remover_termo_saude(global_apps, SimpleNamespace(connection=connection, execute=executar))

    def test_com_aceite_de_saude_recusa(self):
        from aranha_estetica.models import AceiteTermo, Cliente, VersaoTermo

        AceiteTermo.registrar(Cliente.objects.create(nome='C', telefone='11911112222'),
                              VersaoTermo.saude_vigente())
        with self.assertRaises(RuntimeError):
            self._reverter()
        self.assertIsNotNone(VersaoTermo.saude_vigente())

    def test_sem_aceite_apaga_o_termo_saude(self):
        from aranha_estetica.models import VersaoTermo

        self._reverter()
        self.assertFalse(VersaoTermo.objects.filter(tipo='SAUDE').exists())
        self.assertIsNotNone(VersaoTermo.lgpd_vigente())


# ─── 0048: agenda 4 -> 2 (agenda_horario + agenda_excecao) ─────────────────
M0047 = '0047_termo_saude_reembolso_promocao_geral'
M0048 = '0048_agenda_unificada'


def _local_dt(dia, hora, minuto=0):
    from datetime import datetime, time
    return timezone.make_aware(datetime.combine(dia, time(hora, minuto)))


@tag('migracoes')
class AgendaUnificada0048Tests(_MigracaoBase):
    """Dados antigos -> novos: grade ISO, bloqueio (mesmo id, RRULE normalizada),
    folga/horario diferente (mapa no log), feriado nacional/local/recesso/inativo."""
    migrate_from = M0047
    migrate_to = M0048

    def preparar(self, apps):
        from datetime import time

        Profissional = apps.get_model(APP, 'Profissional')
        Disp = apps.get_model(APP, 'DisponibilidadeProfissional')
        Exc = apps.get_model(APP, 'ExcecaoDisponibilidade')
        Bloq = apps.get_model(APP, 'BloqueioAgenda')
        Feriado = apps.get_model(APP, 'Feriado')
        Cliente = apps.get_model(APP, 'Cliente')
        Cliente.objects.create(nome='Cliente', telefone='11911112222')  # banco "operando"

        a = Profissional.objects.create(nome='Prof A')
        b = Profissional.objects.create(nome='Prof B')
        self.a, self.b = a.pk, b.pk
        # grade antiga: 1=Dom .. 7=Sab (turno dividido na segunda)
        Disp.objects.create(profissional=a, dia_semana=1, hora_inicio=time(9), hora_fim=time(12))
        Disp.objects.create(profissional=a, dia_semana=2, hora_inicio=time(9), hora_fim=time(12))
        Disp.objects.create(profissional=a, dia_semana=2, hora_inicio=time(14), hora_fim=time(18))
        Disp.objects.create(profissional=a, dia_semana=7, hora_inicio=time(8), hora_fim=time(12))

        d = date(2031, 3, 10)
        self.d = d
        self.folga = Exc.objects.create(profissional=a, data=d, tipo='FOLGA')
        self.folga_horas = Exc.objects.create(profissional=a, data=d + timedelta(days=1), tipo='FOLGA',
                                              hora_inicio=time(9), hora_fim=time(10), motivo='Consulta')
        self.hd = Exc.objects.create(profissional=a, data=d + timedelta(days=2), tipo='HORARIO_DIFERENTE',
                                     hora_inicio=time(10), hora_fim=time(14), motivo='Meio periodo')
        # legado anterior a validacao da tela: fim <= inicio zerava o dia
        self.hd_invalido = Exc.objects.create(profissional=b, data=d, tipo='HORARIO_DIFERENTE',
                                              hora_inicio=time(14), hora_fim=time(9))
        self.folga_b = Exc.objects.create(profissional=b, data=d + timedelta(days=1), tipo='FOLGA')
        self.hd_invalido_com_folga = Exc.objects.create(
            profissional=b, data=d + timedelta(days=1), tipo='HORARIO_DIFERENTE',
            hora_inicio=time(15), hora_fim=time(15))

        self.feriados_antes = Feriado.objects.count()  # semeados pela 0012 + os abaixo
        self.antigo = (timezone.now() - timedelta(days=300)).replace(microsecond=0)
        self.fer_nacional = Feriado.objects.create(data=date(2031, 1, 1), nome='Confraternizacao',
                                                   escopo='NACIONAL', bloqueia_agendamento=True)
        Feriado.objects.filter(pk=self.fer_nacional.pk).update(criado_em=self.antigo)
        self.fer_inativo = Feriado.objects.create(data=date(2031, 4, 21), nome='Tiradentes', escopo='NACIONAL',
                                                  bloqueia_agendamento=False, observacao='clinica abre')
        self.fer_local = Feriado.objects.create(data=date(2031, 7, 9), nome='Revolucao', escopo='LOCAL')
        self.recesso = Feriado.objects.create(data=date(2031, 12, 24), nome='Recesso', escopo='CLINICA')
        self.feriados_antes += 4

        ini = _local_dt(date(2031, 3, 5), 12)
        fim = ini + timedelta(hours=1)
        self.b_semanal = Bloq.objects.create(profissional=a, data_hora_inicio=ini, data_hora_fim=fim, motivo='Almoco',
                                             regra_recorrencia='FREQ=WEEKLY;BYDAY=WE',
                                             recorrencia_ate=date(2031, 6, 30))
        self.b_global = Bloq.objects.create(profissional=None, data_hora_inicio=ini, data_hora_fim=fim)
        self.b_gcal = Bloq.objects.create(profissional=a, data_hora_inicio=ini, data_hora_fim=fim,
                                          motivo='gcal:Reuniao')
        self.b_gcal_global = Bloq.objects.create(profissional=None, data_hora_inicio=ini, data_hora_fim=fim,
                                                 motivo='gcal:sem profissional')
        self.b_sujo = Bloq.objects.create(profissional=b, data_hora_inicio=ini, data_hora_fim=fim,
                                          regra_recorrencia='rrule:freq=weekly;byday=fr,mo;wkst=MO;')
        self.b_count = Bloq.objects.create(profissional=b, data_hora_inicio=_local_dt(date(2031, 3, 1), 10),
                                           data_hora_fim=_local_dt(date(2031, 3, 1), 11),
                                           regra_recorrencia='FREQ=DAILY;COUNT=3')
        self.b_ate_sem_regra = Bloq.objects.create(profissional=a, data_hora_inicio=ini, data_hora_fim=fim,
                                                   recorrencia_ate=date(2031, 5, 1))

    def test_dados_migrados(self):
        from datetime import time

        AgendaHorario = self.apps.get_model(APP, 'AgendaHorario')
        AgendaExcecao = self.apps.get_model(APP, 'AgendaExcecao')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')

        # grade: dia ISO (Dom 1->7, Seg 2->1, Sab 7->6), turno dividido preservado
        self.assertEqual(
            set(AgendaHorario.objects.values_list('profissional_id', 'dia_semana', 'hora_inicio', 'hora_fim')),
            {(self.a, 7, time(9), time(12)), (self.a, 1, time(9), time(12)),
             (self.a, 1, time(14), time(18)), (self.a, 6, time(8), time(12))},
        )

        # bloqueios: MESMO id, recorrencia normalizada, origem GCAL so com profissional
        def bl(obj):
            return AgendaExcecao.objects.get(pk=obj.pk)

        semanal = bl(self.b_semanal)
        self.assertEqual((semanal.tipo, semanal.origem, semanal.profissional_id, semanal.motivo),
                         ('BLOQUEIO', 'MANUAL', self.a, 'Almoco'))
        self.assertEqual((semanal.regra_recorrencia, semanal.data_fim_recorrencia),
                         ('FREQ=WEEKLY;BYDAY=WE', date(2031, 6, 30)))
        self.assertEqual((semanal.data_hora_inicio, semanal.data_hora_fim),
                         (self.b_semanal.data_hora_inicio, self.b_semanal.data_hora_fim))
        self.assertIsNone(semanal.data_inicio)
        glob = bl(self.b_global)
        self.assertEqual((glob.profissional_id, glob.regra_recorrencia, glob.motivo), (None, None, ''))
        self.assertEqual(bl(self.b_gcal).origem, 'GCAL')
        self.assertEqual((bl(self.b_gcal_global).origem, bl(self.b_gcal_global).motivo),
                         ('MANUAL', 'gcal:sem profissional'))
        self.assertEqual(bl(self.b_sujo).regra_recorrencia, 'FREQ=WEEKLY;BYDAY=MO,FR')
        count = bl(self.b_count)
        self.assertEqual((count.regra_recorrencia, count.data_fim_recorrencia), ('FREQ=DAILY', date(2031, 3, 3)))
        sem_regra = bl(self.b_ate_sem_regra)
        self.assertEqual((sem_regra.regra_recorrencia, sem_regra.data_fim_recorrencia), (None, None))

        log = LogAuditoria.objects.get(acao='migration 0048: agenda unificada')
        det = log.detalhes
        self.assertEqual(log.tabela, 'agenda_excecao')
        self.assertEqual(det['bloqueio_agenda'], 'ids preservados')
        self.assertEqual(set(det['recorrencia_normalizada']), {str(self.b_sujo.pk), str(self.b_count.pk)})
        self.assertEqual(det['recorrencia_normalizada'][str(self.b_count.pk)]['para'],
                         ['FREQ=DAILY', '2031-03-03'])
        self.assertEqual(det['fim_recorrencia_sem_regra_descartado'], [self.b_ate_sem_regra.pk])

        # excecoes: ids novos, mapa no log; saneamentos registrados
        mapa = det['excecao_disponibilidade']

        def ex(obj):
            return AgendaExcecao.objects.get(pk=mapa[str(obj.pk)])

        folga = ex(self.folga)
        self.assertEqual((folga.tipo, folga.profissional_id, folga.data_inicio, folga.data_fim, folga.motivo,
                          folga.origem, folga.ativo), ('FOLGA', self.a, self.d, self.d, '', 'MANUAL', True))
        folga_horas = ex(self.folga_horas)
        self.assertEqual((folga_horas.tipo, folga_horas.hora_inicio, folga_horas.motivo), ('FOLGA', None, 'Consulta'))
        hd = ex(self.hd)
        self.assertEqual((hd.tipo, hd.data_inicio, hd.data_fim, hd.hora_inicio, hd.hora_fim, hd.motivo),
                         ('HORARIO_DIFERENTE', self.d + timedelta(days=2), self.d + timedelta(days=2),
                          time(10), time(14), 'Meio periodo'))
        invalido = ex(self.hd_invalido)
        self.assertEqual((invalido.tipo, invalido.hora_inicio, invalido.profissional_id), ('FOLGA', None, self.b))
        self.assertNotIn(str(self.hd_invalido_com_folga.pk), mapa)
        self.assertEqual(ex(self.folga_b).tipo, 'FOLGA')
        self.assertEqual(det['horario_diferente_invalido_vira_folga'], [self.hd_invalido.pk])
        self.assertEqual(det['horario_diferente_invalido_descartado'], [self.hd_invalido_com_folga.pk])
        self.assertEqual(det['folga_com_horas_ignoradas'], [self.folga_horas.pk])
        self.assertEqual(len(mapa), 5)

        # feriados = FOLGA da clinica; inativo continua registrado (nao volta no carregar_feriados)
        fer = det['feriado']
        self.assertEqual(len(fer), self.feriados_antes)
        globais = AgendaExcecao.objects.filter(profissional__isnull=True, tipo='FOLGA')
        self.assertEqual(globais.count(), self.feriados_antes)

        def fe(obj):
            return AgendaExcecao.objects.get(pk=fer[str(obj.pk)])

        nacional = fe(self.fer_nacional)
        self.assertEqual((nacional.origem, nacional.ativo, nacional.motivo, nacional.data_inicio, nacional.data_fim),
                         ('FERIADO_NACIONAL', True, 'Confraternizacao', date(2031, 1, 1), date(2031, 1, 1)))
        self.assertEqual(nacional.criado_em, self.antigo)
        inativo = fe(self.fer_inativo)
        self.assertEqual((inativo.origem, inativo.ativo, inativo.observacao), ('FERIADO_NACIONAL', False, 'clinica abre'))
        self.assertEqual(fe(self.fer_local).origem, 'FERIADO_LOCAL')
        recesso = fe(self.recesso)
        self.assertEqual((recesso.origem, recesso.observacao), ('RECESSO', ''))

        # sequencia depois dos ids explicitos: proximo id nao colide
        novo = AgendaExcecao.objects.create(tipo='FOLGA', profissional_id=self.a,
                                            data_inicio=date(2031, 9, 1), data_fim=date(2031, 9, 3))
        self.assertGreater(novo.pk, max(AgendaExcecao.objects.exclude(pk=novo.pk).values_list('pk', flat=True)))

        tabelas = connection.introspection.table_names()
        for antiga in ('disponibilidade_profissional', 'excecao_disponibilidade', 'bloqueio_agenda', 'feriado'):
            self.assertNotIn(antiga, tabelas)

    def test_regex_do_banco_e_o_espelho_python(self):
        import importlib

        from aranha_estetica.utils.recorrencia import REGEX_SUBCONJUNTO

        mig = importlib.import_module('aranha_estetica.migrations.0048_agenda_unificada')
        self.assertEqual(mig.REGEX_RRULE, REGEX_SUBCONJUNTO)


@tag('migracoes')
class AgendaUnificadaReversa0048Tests(_MigracaoBase):
    """Reverso (dev): agenda nova -> 4 tabelas antigas (e sobe de novo no tearDown)."""
    migrate_from = M0048
    migrate_to = M0047

    def preparar(self, apps):
        from datetime import time

        Profissional = apps.get_model(APP, 'Profissional')
        AgendaHorario = apps.get_model(APP, 'AgendaHorario')
        AgendaExcecao = apps.get_model(APP, 'AgendaExcecao')
        p = Profissional.objects.create(nome='Prof R')
        self.p = p.pk
        AgendaHorario.objects.create(profissional=p, dia_semana=1, hora_inicio=time(9), hora_fim=time(12))
        AgendaHorario.objects.create(profissional=p, dia_semana=7, hora_inicio=time(9), hora_fim=time(12))
        d = date(2032, 5, 10)
        self.d = d
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=p, data_inicio=d, data_fim=d + timedelta(days=2),
                                     motivo='Ferias')
        self.inativa = AgendaExcecao.objects.create(tipo='FOLGA', profissional=p, data_inicio=d + timedelta(days=9),
                                                    data_fim=d + timedelta(days=9), ativo=False)
        AgendaExcecao.objects.create(tipo='HORARIO_DIFERENTE', profissional=p, data_inicio=d + timedelta(days=5),
                                     data_fim=d + timedelta(days=5), hora_inicio=time(13), hora_fim=time(15))
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=None, origem='RECESSO', data_inicio=date(2032, 12, 24),
                                     data_fim=date(2032, 12, 25), motivo='Recesso de Natal')
        AgendaExcecao.objects.create(tipo='FOLGA', profissional=None, origem='FERIADO_NACIONAL',
                                     data_inicio=date(2032, 4, 21), data_fim=date(2032, 4, 21), motivo='Tiradentes',
                                     ativo=False)
        self.bloqueio = AgendaExcecao.objects.create(
            tipo='BLOQUEIO', profissional=p, data_hora_inicio=_local_dt(d, 12), data_hora_fim=_local_dt(d, 13),
            regra_recorrencia='FREQ=WEEKLY;BYDAY=MO', data_fim_recorrencia=date(2032, 8, 1))
        self.gcal = AgendaExcecao.objects.create(
            tipo='BLOQUEIO', profissional=p, origem='GCAL', origem_ref='evt-1',
            data_hora_inicio=_local_dt(d, 16), data_hora_fim=_local_dt(d, 17), motivo='gcal:x')

    def test_restaura_modelo_antigo(self):
        from datetime import time

        Disp = self.apps.get_model(APP, 'DisponibilidadeProfissional')
        Exc = self.apps.get_model(APP, 'ExcecaoDisponibilidade')
        Bloq = self.apps.get_model(APP, 'BloqueioAgenda')
        Feriado = self.apps.get_model(APP, 'Feriado')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        self.assertEqual(set(Disp.objects.filter(profissional_id=self.p).values_list('dia_semana', flat=True)), {2, 1})
        folgas = sorted(Exc.objects.filter(profissional_id=self.p, tipo='FOLGA').values_list('data', flat=True))
        self.assertEqual(folgas, [self.d, self.d + timedelta(days=1), self.d + timedelta(days=2)])
        hd = Exc.objects.get(profissional_id=self.p, tipo='HORARIO_DIFERENTE')
        self.assertEqual((hd.data, hd.hora_inicio, hd.hora_fim), (self.d + timedelta(days=5), time(13), time(15)))
        b = Bloq.objects.get(pk=self.bloqueio.pk)
        self.assertEqual((b.regra_recorrencia, b.recorrencia_ate), ('FREQ=WEEKLY;BYDAY=MO', date(2032, 8, 1)))
        self.assertEqual(Bloq.objects.get(pk=self.gcal.pk).motivo, 'gcal:x')
        recesso = Feriado.objects.filter(nome='Recesso de Natal', escopo='CLINICA')
        self.assertEqual(sorted(recesso.values_list('data', flat=True)), [date(2032, 12, 24), date(2032, 12, 25)])
        self.assertFalse(Feriado.objects.get(data=date(2032, 4, 21), escopo='NACIONAL').bloqueia_agendamento)
        log = LogAuditoria.objects.get(acao__startswith='migration 0048 (reversa)')
        self.assertEqual(log.detalhes, {'excecao_inativa': [self.inativa.pk], 'origem_ref': [self.gcal.pk]})


@tag('migracoes')
class AgendaUnificada0048AbortaTests(TransactionTestCase):
    """Pre-checagem: janelas sobrepostas e RRULE fora do subconjunto abortam a
    0048 inteira (nada aplicado) com os ids na mensagem."""
    serialized_rollback = True

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([(APP, M0047)])
        self.antes = executor.loader.project_state([(APP, M0047)]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _migrar_0048(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([(APP, M0048)])

    def _nao_aplicou(self):
        from django.db.migrations.recorder import MigrationRecorder

        self.assertNotIn((APP, M0048), MigrationRecorder(connection).applied_migrations())
        self.assertNotIn('agenda_horario', connection.introspection.table_names())

    def test_janelas_sobrepostas_abortam(self):
        from datetime import time

        Profissional = self.antes.get_model(APP, 'Profissional')
        Disp = self.antes.get_model(APP, 'DisponibilidadeProfissional')
        p = Profissional.objects.create(nome='Prof Sobreposta')
        a = Disp.objects.create(profissional=p, dia_semana=2, hora_inicio=time(9), hora_fim=time(12))
        b = Disp.objects.create(profissional=p, dia_semana=2, hora_inicio=time(11), hora_fim=time(13))
        Disp.objects.create(profissional=p, dia_semana=2, hora_inicio=time(13), hora_fim=time(14))  # encosta: ok
        Disp.objects.create(profissional=p, dia_semana=3, hora_inicio=time(11), hora_fim=time(13))  # outro dia
        with self.assertRaises(RuntimeError) as ctx:
            self._migrar_0048()
        msg = str(ctx.exception)
        self.assertIn('0048', msg)
        self.assertIn('janelas de expediente sobrepostas', msg)
        self.assertIn(f'[({a.pk}, {b.pk})]', msg)
        self._nao_aplicou()
        Disp.objects.filter(pk=b.pk).delete()  # corrigido: o tearDown sobe ate o fim

    def test_rrule_fora_do_subconjunto_aborta(self):
        Bloq = self.antes.get_model(APP, 'BloqueioAgenda')
        ini = timezone.now()
        ruins = [
            Bloq.objects.create(data_hora_inicio=ini, data_hora_fim=ini + timedelta(hours=1), regra_recorrencia=r)
            for r in ('FREQ=YEARLY;BYMONTH=12', 'FREQ=MONTHLY;BYDAY=5FR', 'FREQ=WEEKLY;BYHOUR=10', 'lixo')
        ]
        ok = Bloq.objects.create(data_hora_inicio=ini, data_hora_fim=ini + timedelta(hours=1),
                                 regra_recorrencia='FREQ=MONTHLY;BYDAY=-1FR')
        with self.assertRaises(RuntimeError) as ctx:
            self._migrar_0048()
        msg = str(ctx.exception)
        self.assertIn('regra de recorrencia fora do subconjunto', msg)
        for r in ruins:
            self.assertIn(f'({r.pk}, ', msg)
        self.assertNotIn(f'({ok.pk}, ', msg)
        self._nao_aplicou()
        Bloq.objects.filter(pk__in=[r.pk for r in ruins]).delete()


# Copia CONGELADA do SlotService anterior a 0048 (services/disponibilidade.py +
# BloqueioAgenda.expandir_ocorrencias @ 0047), sobre os models historicos: a
# referencia de "antes" da caracterizacao abaixo.
def _slots_legado(apps, profissional, data_selecionada, procedimento):
    from datetime import datetime, time

    from dateutil.rrule import rrulestr
    from django.db.models import Q

    Feriado = apps.get_model(APP, 'Feriado')
    Exc = apps.get_model(APP, 'ExcecaoDisponibilidade')
    Disp = apps.get_model(APP, 'DisponibilidadeProfissional')
    Bloq = apps.get_model(APP, 'BloqueioAgenda')
    Atendimento = apps.get_model(APP, 'Atendimento')

    def aware(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone()) if timezone.is_naive(dt) else dt

    def expandir(b, range_inicio, range_fim):
        duracao = b.data_hora_fim - b.data_hora_inicio
        if not b.regra_recorrencia:
            if b.data_hora_fim > range_inicio and b.data_hora_inicio < range_fim:
                return [(b.data_hora_inicio, b.data_hora_fim)]
            return []
        inicio_local = timezone.localtime(b.data_hora_inicio)
        try:
            rule = rrulestr(f'RRULE:{b.regra_recorrencia}', dtstart=inicio_local, forceset=True)
        except (ValueError, TypeError):
            return [(b.data_hora_inicio, b.data_hora_fim)]
        fim_busca = range_fim
        if b.recorrencia_ate:
            limite = aware(datetime.combine(b.recorrencia_ate + timedelta(days=1), time.min))
            if limite < fim_busca:
                fim_busca = limite
        try:
            candidatos = rule.between(range_inicio - duracao, fim_busca, inc=True)
        except (ValueError, TypeError):
            return [(b.data_hora_inicio, b.data_hora_fim)]
        return [(i, i + duracao) for i in candidatos
                if not fim_busca <= i and i + duracao > range_inicio and i < range_fim]

    if Feriado.objects.filter(data=data_selecionada, bloqueia_agendamento=True).exists():
        return []
    agora = timezone.localtime()
    if data_selecionada < agora.date():
        return []
    limite_min_notice = agora + timedelta(hours=profissional.min_notice_horas)
    if data_selecionada > (agora + timedelta(days=profissional.max_advance_dias)).date():
        return []
    excecao_horario = None
    for ex in Exc.objects.filter(profissional=profissional, data=data_selecionada):
        if ex.tipo == 'FOLGA':
            return []
        if ex.tipo == 'HORARIO_DIFERENTE' and ex.hora_inicio and ex.hora_fim:
            excecao_horario = ex
    if excecao_horario:
        janelas = [(excecao_horario.hora_inicio, excecao_horario.hora_fim)]
    else:
        janelas = [(d.hora_inicio, d.hora_fim) for d in Disp.objects.filter(
            profissional=profissional, dia_semana=data_selecionada.isoweekday() % 7 + 1)]
        if not janelas:
            return []
    dia_inicio = aware(datetime.combine(data_selecionada, time.min))
    dia_fim = dia_inicio + timedelta(days=1)
    ocupados = []
    for ag in Atendimento.objects.filter(
        profissional=profissional, data_hora_inicio__lt=dia_fim, data_hora_fim__gt=dia_inicio,
        status__in=('PENDENTE', 'AGENDADO', 'CONFIRMADO'),
    ).select_related('procedimento'):
        ocupados.append((ag.data_hora_inicio,
                         ag.data_hora_fim + timedelta(minutes=ag.procedimento.buffer_minutos or 0)))
    filtro = Q(profissional=profissional) | Q(profissional__isnull=True)
    bloqueios = [(b.data_hora_inicio, b.data_hora_fim) for b in Bloq.objects.filter(
        filtro, regra_recorrencia='').filter(data_hora_inicio__lt=dia_fim, data_hora_fim__gt=dia_inicio)]
    for b in Bloq.objects.filter(filtro).exclude(regra_recorrencia=''):
        bloqueios.extend(expandir(b, dia_inicio, dia_fim))
    duracao = timedelta(minutes=procedimento.duracao_minutos) if procedimento.duracao_minutos else timedelta(minutes=30)
    buffer_proc = timedelta(minutes=procedimento.buffer_minutos or 0)
    livres = set()
    for hora_ini, hora_fim in janelas:
        atual = aware(datetime.combine(data_selecionada, hora_ini))
        fim_exp = aware(datetime.combine(data_selecionada, hora_fim))
        while atual + duracao <= fim_exp:
            if atual >= limite_min_notice:
                fim_slot = atual + duracao
                if not any(atual < of and fim_slot + buffer_proc > oi for oi, of in ocupados) and not any(
                        atual < bf and fim_slot > bi for bi, bf in bloqueios):
                    livres.add(timezone.localtime(atual).strftime('%H:%M'))
            atual += timedelta(minutes=30)
    return sorted(livres)


@tag('migracoes')
class SlotsAntesDepois0048Tests(_MigracaoBase):
    """Caracterizacao: os MESMOS horarios livres antes (modelo antigo, motor
    antigo congelado acima) e depois da 0048 (SlotService atual) — feriado ativo
    e inativo, recesso, folga, horario diferente (valido e invalido), bloqueio
    pontual/global/recorrente (WEEKLY com fim, DAILY;COUNT, UNTIL em UTC),
    atendimento com buffer e antecedencia minima/maxima."""
    migrate_from = M0047
    migrate_to = M0048
    DIAS = range(-1, 16)

    def setUp(self):
        from datetime import datetime, time

        hoje = timezone.localdate()
        self.hoje = hoje
        self.agora_fixa = timezone.make_aware(datetime.combine(hoje, time(10, 17)))
        super().setUp()

    def _cenario(self):
        return [(p, self.hoje + timedelta(days=n), proc)
                for p in (self.pa, self.pb) for n in self.DIAS for proc in (self.p60, self.p30)]

    def preparar(self, apps):
        from datetime import datetime, time
        from datetime import timezone as dt_tz

        Profissional = apps.get_model(APP, 'Profissional')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Disp = apps.get_model(APP, 'DisponibilidadeProfissional')
        Exc = apps.get_model(APP, 'ExcecaoDisponibilidade')
        Bloq = apps.get_model(APP, 'BloqueioAgenda')
        Feriado = apps.get_model(APP, 'Feriado')
        Cliente = apps.get_model(APP, 'Cliente')
        Atendimento = apps.get_model(APP, 'Atendimento')
        hoje = self.hoje

        def dia(n):
            return hoje + timedelta(days=n)

        a = Profissional.objects.create(nome='Prof A', min_notice_horas=2, max_advance_dias=12)
        b = Profissional.objects.create(nome='Prof B', min_notice_horas=0, max_advance_dias=30)
        p60 = Procedimento.objects.create(nome='Peeling', duracao_minutos=60, buffer_minutos=30)
        p30 = Procedimento.objects.create(nome='Limpeza', duracao_minutos=30, buffer_minutos=0)
        self.pa, self.pb, self.p60, self.p30 = a.pk, b.pk, p60.pk, p30.pk
        for d in (2, 3, 4, 5, 6):  # seg-sex, antigo 1=Dom
            Disp.objects.create(profissional=a, dia_semana=d, hora_inicio=time(9), hora_fim=time(12))
            Disp.objects.create(profissional=a, dia_semana=d, hora_inicio=time(14), hora_fim=time(18))
        Disp.objects.create(profissional=a, dia_semana=7, hora_inicio=time(9), hora_fim=time(13))
        for d in range(1, 8):
            Disp.objects.create(profissional=b, dia_semana=d, hora_inicio=time(8), hora_fim=time(17))

        def feriado(n, escopo, bloqueia, nome):
            Feriado.objects.update_or_create(data=dia(n), escopo=escopo,
                                             defaults={'nome': nome, 'bloqueia_agendamento': bloqueia})

        feriado(3, 'LOCAL', True, 'Feriado municipal')
        feriado(4, 'NACIONAL', False, 'Nacional em que a clinica abre')
        feriado(5, 'CLINICA', True, 'Recesso')
        Exc.objects.create(profissional=a, data=dia(6), tipo='FOLGA')
        Exc.objects.create(profissional=a, data=dia(7), tipo='HORARIO_DIFERENTE',
                           hora_inicio=time(10), hora_fim=time(11, 30))
        Exc.objects.create(profissional=b, data=dia(8), tipo='HORARIO_DIFERENTE',
                           hora_inicio=time(15), hora_fim=time(9))  # invalido: zera o dia

        def local(n, h, m=0):
            return timezone.make_aware(datetime.combine(dia(n), time(h, m)))

        Bloq.objects.create(profissional=a, data_hora_inicio=local(-7, 15), data_hora_fim=local(-7, 16),
                            regra_recorrencia='FREQ=WEEKLY', recorrencia_ate=dia(7))
        Bloq.objects.create(profissional=b, data_hora_inicio=local(-1, 12), data_hora_fim=local(-1, 13),
                            regra_recorrencia='FREQ=DAILY;INTERVAL=2;COUNT=4')
        # UNTIL em UTC (o motor antigo so aceitava UNTIL com Z quando DTSTART e aware)
        until = local(9, 20, 59).astimezone(dt_tz.utc).strftime('%Y%m%dT%H%M%SZ')
        Bloq.objects.create(profissional=b, data_hora_inicio=local(-3, 16, 30), data_hora_fim=local(-3, 17),
                            regra_recorrencia=f'FREQ=WEEKLY;BYDAY=MO,WE;UNTIL={until}')
        Bloq.objects.create(profissional=None, data_hora_inicio=local(2, 9), data_hora_fim=local(2, 10, 30))
        Bloq.objects.create(profissional=b, data_hora_inicio=local(2, 16), data_hora_fim=local(2, 16, 30))

        cli = Cliente.objects.create(nome='Cliente', telefone='11911112222')
        Atendimento.objects.create(cliente=cli, profissional=a, procedimento=p60, status='AGENDADO',
                                   data_hora_inicio=local(1, 10), data_hora_fim=local(1, 11))
        Atendimento.objects.create(cliente=cli, profissional=b, procedimento=p30, status='CONFIRMADO',
                                   data_hora_inicio=local(2, 9), data_hora_fim=local(2, 9, 30))
        Atendimento.objects.create(cliente=cli, profissional=b, procedimento=p60, status='CANCELADO',
                                   data_hora_inicio=local(2, 11), data_hora_fim=local(2, 12))

        P = {x.pk: x for x in Profissional.objects.all()}
        R = {x.pk: x for x in Procedimento.objects.all()}
        with mock.patch('django.utils.timezone.now', return_value=self.agora_fixa):
            self.antes = {
                (p, d, r): _slots_legado(apps, P[p], d, R[r]) for p, d, r in self._cenario()
            }

    def test_mesmos_slots_antes_e_depois(self):
        from aranha_estetica.models import Procedimento, Profissional
        from aranha_estetica.services.disponibilidade import SlotService

        _subir_ate_o_fim()  # o SlotService usa os models reais: esquema final (nomes da 0054)
        P = {x.pk: x for x in Profissional.objects.filter(pk__in=[self.pa, self.pb])}
        R = {x.pk: x for x in Procedimento.objects.filter(pk__in=[self.p60, self.p30])}
        with mock.patch('django.utils.timezone.now', return_value=self.agora_fixa):
            depois = {(p, d, r): SlotService.slots_livres(P[p], d, R[r]) for p, d, r in self._cenario()}
        diferencas = {k: (v, depois[k]) for k, v in self.antes.items() if v != depois[k]}
        self.assertEqual(diferencas, {})
        # o cenario exercita os dois lados (nao e "vazio == vazio")
        cheios = sum(1 for v in self.antes.values() if v)
        self.assertGreater(cheios, 20)
        self.assertLess(cheios, len(self.antes))
        hoje = self.hoje
        self.assertEqual(self.antes[(self.pb, hoje + timedelta(days=3), self.p30)], [])  # feriado local
        self.assertTrue(self.antes[(self.pb, hoje + timedelta(days=4), self.p30)])      # feriado inativo
        self.assertEqual(self.antes[(self.pb, hoje + timedelta(days=8), self.p30)], [])  # HD invalido


# ─── 0049: tokens em hash (link_acesso) ────────────────────────────────────
M0049 = '0049_link_acesso'


def _sha(token):
    import hashlib
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _subir_ate_o_fim():
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


@tag('migracoes')
class LinksEmHash0049Tests(_MigracaoBase):
    """Tokens em texto puro -> link_acesso (sha256): os links ja enviados continuam
    abrindo pelo token em claro, com validade/uso derivados dos dados antigos; o
    que nao tinha rota nao migra; colunas antigas somem."""
    migrate_from = M0048
    migrate_to = M0049

    def preparar(self, apps):
        Profissional = apps.get_model(APP, 'Profissional')
        Cliente = apps.get_model(APP, 'Cliente')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Atendimento = apps.get_model(APP, 'Atendimento')
        Notificacao = apps.get_model(APP, 'Notificacao')
        AvaliacaoNPS = apps.get_model(APP, 'AvaliacaoNPS')
        FormularioAnamnese = apps.get_model(APP, 'FormularioAnamnese')
        RespostaAnamnese = apps.get_model(APP, 'RespostaAnamnese')
        ListaEspera = apps.get_model(APP, 'ListaEspera')

        agora = timezone.now().replace(microsecond=0)
        self.agora = agora
        prof = Profissional.objects.create(nome='Prof L', slug='prof-l', ics_token='tok-ics-prof')
        self.prof = prof.pk
        Profissional.objects.create(nome='Prof Sem Ics', slug='prof-sem-ics')
        cli = Cliente.objects.create(nome='Cli L', telefone='11911113333', token_descadastro='tok-descad')
        self.cli = cli.pk
        # dado sujo: o mesmo token em duas tabelas (o UNIQUE era so por tabela)
        self.prof_dup = Profissional.objects.create(nome='Prof Dup', slug='prof-dup', ics_token='tok-descad').pk
        Cliente.objects.create(nome='Cli X', telefone='11911114444', token_descadastro='tok-descad-excluida',
                               deletado_em=agora)
        proc = Procedimento.objects.create(nome='Proc L', duracao_minutos=60)
        self.ini = agora + timedelta(days=3)
        at = Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc, status='AGENDADO',
                                        data_hora_inicio=self.ini, data_hora_fim=self.ini + timedelta(hours=1),
                                        token_cancelamento='tok-gerenciar')
        self.at = at.pk
        passado = agora - timedelta(days=2)
        at_real = Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc, status='REALIZADO',
                                             data_hora_inicio=passado, data_hora_fim=passado + timedelta(hours=1),
                                             token_cancelamento='tok-gerenciar-passado')
        self.passado = passado

        def notif(tipo, canal, token, atendimento=at, **kw):
            return Notificacao.objects.create(atendimento=atendimento, tipo=tipo, canal=canal, token=token, **kw).pk

        self.n_d1 = notif('LEMBRETE', 'WHATSAPP', 'tok-d1', resposta='CONFIRMOU',
                          respondido_em=agora - timedelta(hours=1))
        self.n_2h = notif('LEMBRETE_2H', 'WHATSAPP', 'tok-2h')
        self.n_termo = notif('TERMO', 'EMAIL', 'tok-termo')
        self.n_termo_legado = notif('LEMBRETE', 'EMAIL', 'tok-termo-legado')
        self.n_nps = notif('NPS', 'WHATSAPP', 'tok-nps', atendimento=at_real)
        Notificacao.objects.filter(pk=self.n_nps).update(criado_em=agora - timedelta(days=1))
        self.avaliacao_em = AvaliacaoNPS.objects.create(atendimento=at_real, nota=9).criado_em
        self.n_sms = notif('LEMBRETE', 'SMS', 'tok-lembrete-sms')          # /confirmar/ exigia WHATSAPP
        self.n_canc = notif('CANCELAMENTO', 'EMAIL', 'tok-cancelamento')    # nenhuma rota
        notif('LEMBRETE', 'WHATSAPP', None)
        notif('LEMBRETE', 'WHATSAPP', '')

        form = FormularioAnamnese.objects.create(nome='F', tipo='ANAMNESE', schema_json=[])
        self.r_aberta = RespostaAnamnese.objects.create(formulario=form, cliente=cli, atendimento=at,
                                                        token='tok-ficha')
        self.r_respondida = RespostaAnamnese.objects.create(
            formulario=form, cliente=cli, token='tok-ficha-respondida', respostas_json={'a': 1},
            respondida_em=agora - timedelta(days=1),
        )
        ListaEspera.objects.create(cliente=cli, procedimento=proc, data_desejada=self.ini.date(),
                                   token_reserva='tok-reserva', expira_em=agora)

    def test_hash_validade_uso_e_o_que_nao_migra(self):
        LinkAcesso = self.apps.get_model(APP, 'LinkAcesso')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')

        def link(token):
            return LinkAcesso.objects.get(token_hash=_sha(token))

        fim = self.ini + timedelta(hours=1)
        esperado = {
            'tok-gerenciar': ('GERENCIAR_AGENDAMENTO', 'atendimento_id', self.at, self.ini, None),
            'tok-d1': ('CONFIRMAR_PRESENCA', 'notificacao_id', self.n_d1, self.ini, self.agora - timedelta(hours=1)),
            'tok-2h': ('CONFIRMAR_PRESENCA', 'notificacao_id', self.n_2h, self.ini, None),
            'tok-termo': ('TERMO', 'notificacao_id', self.n_termo, fim, None),
            'tok-termo-legado': ('TERMO', 'notificacao_id', self.n_termo_legado, fim, None),
            'tok-nps': ('NPS', 'notificacao_id', self.n_nps, self.agora + timedelta(days=6), self.avaliacao_em),
            'tok-ficha': ('FORMULARIO', 'resposta_formulario_id', self.r_aberta.pk,
                          self.r_aberta.criado_em + timedelta(days=60), None),
            'tok-ficha-respondida': ('FORMULARIO', 'resposta_formulario_id', self.r_respondida.pk,
                                     self.r_respondida.criado_em + timedelta(days=60), self.agora - timedelta(days=1)),
            'tok-descad': ('DESCADASTRO', 'cliente_id', self.cli, None, None),
            'tok-ics-prof': ('FEED_ICS', 'profissional_id', self.prof, None, None),
        }
        for token, (finalidade, campo, alvo, expira, usado) in esperado.items():
            with self.subTest(token=token):
                lk = link(token)
                self.assertEqual(lk.token_hash, _sha(token))
                self.assertEqual(lk.finalidade, finalidade)
                self.assertEqual(getattr(lk, campo), alvo)
                self.assertEqual(lk.expira_em, expira)
                self.assertEqual(lk.usado_em, usado)
                self.assertIsNone(lk.revogado_em)
        # migrado de atendimento passado ja nasce vencido (expira no inicio)
        self.assertEqual(link('tok-gerenciar-passado').expira_em, self.passado)
        # sem rota antes = sem link depois; descadastro de excluida nao resolvia
        for token in ('tok-lembrete-sms', 'tok-cancelamento', 'tok-descad-excluida', 'tok-reserva'):
            self.assertFalse(LinkAcesso.objects.filter(token_hash=_sha(token)).exists(), token)
        self.assertEqual(LinkAcesso.objects.count(), len(esperado) + 1)
        log = LogAuditoria.objects.get(acao__startswith='migration 0049')
        self.assertEqual(log.detalhes['migrados'], {
            'GERENCIAR_AGENDAMENTO': 2, 'CONFIRMAR_PRESENCA': 2, 'TERMO': 2, 'NPS': 1,
            'FORMULARIO': 2, 'DESCADASTRO': 1, 'FEED_ICS': 1,
        })
        self.assertEqual(log.detalhes['notificacao_sem_rota_nao_migrada'], [self.n_sms, self.n_canc])
        self.assertEqual(log.detalhes['token_repetido_descartado'],
                         [f'profissional {self.prof_dup} (ja usado por cliente {self.cli})'])
        # colunas de token em texto puro removidas (+ o legado da lista de espera)
        with connection.cursor() as cursor:
            colunas = {
                tabela: {c.name for c in connection.introspection.get_table_description(cursor, tabela)}
                for tabela in ('atendimento', 'notificacao', 'resposta_anamnese', 'cliente', 'profissional',
                               'lista_espera')
            }
        removidas = {('atendimento', 'token_cancelamento'), ('notificacao', 'token'), ('resposta_anamnese', 'token'),
                     ('cliente', 'token_descadastro'), ('profissional', 'ics_token'),
                     ('lista_espera', 'token_reserva'), ('lista_espera', 'expira_em')}
        self.assertEqual({(t, c) for t, c in removidas if c in colunas[t]}, set())

    def test_links_ja_enviados_abrem_as_rotas_publicas(self):
        from django.core.cache import cache

        from aranha_estetica.services import links

        _subir_ate_o_fim()
        cache.clear()
        with self.settings(RATELIMIT_ENABLE=False):
            get = self.client.get
            self.assertEqual(get('/confirmar/tok-d1/').status_code, 200)
            self.assertIs(get('/confirmar/tok-2h/').context['nao_editavel'], False)
            self.assertEqual(get('/termo/tok-termo/').status_code, 200)
            self.assertEqual(get('/termo/tok-termo-legado/').status_code, 200)
            resp = get('/nps/tok-nps/')
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.context['ja_respondeu'])
            self.assertEqual(get('/anamnese/tok-ficha/').status_code, 200)
            self.assertRedirects(get('/anamnese/tok-ficha-respondida/'), '/anamnese/obrigado/',
                                 fetch_redirect_response=False)
            self.assertEqual(get('/lgpd/unsubscribe/tok-descad/').status_code, 200)
            self.assertEqual(get('/agenda/prof-l/feed.ics', {'token': 'tok-ics-prof'}).status_code, 200)
            # o link legado de reagendar (GERENCIAR_AGENDAMENTO) saiu na 0066: rota e link
            dia = (self.ini + timedelta(days=1)).date().isoformat()
            self.assertEqual(get('/reagendar/tok-gerenciar/horarios/', {'data': dia}).status_code, 404)
            # o que nao abria antes continua nao abrindo
            self.assertEqual(get('/confirmar/tok-lembrete-sms/').status_code, 404)
            self.assertEqual(get('/lgpd/unsubscribe/tok-descad-excluida/').status_code, 404)
            self.assertEqual(get('/nps/tok-termo/').status_code, 404)
            self.assertEqual(get('/agenda/prof-dup/feed.ics', {'token': 'tok-descad'}).status_code, 404)
        self.assertEqual(links.resolver(links.DESCADASTRO, 'tok-descad').cliente_id, self.cli)


@tag('migracoes')
class LinksEmHashReversa0049Tests(_MigracaoBase):
    """Reverso (dev): recria as colunas com tokens NOVOS, unicos e nao nulos
    (resposta_anamnese.token volta NOT NULL UNIQUE) — e sobe de novo no tearDown."""
    migrate_from = M0049
    migrate_to = M0048

    def preparar(self, apps):
        Profissional = apps.get_model(APP, 'Profissional')
        Cliente = apps.get_model(APP, 'Cliente')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Atendimento = apps.get_model(APP, 'Atendimento')
        Notificacao = apps.get_model(APP, 'Notificacao')
        FormularioAnamnese = apps.get_model(APP, 'FormularioAnamnese')
        RespostaAnamnese = apps.get_model(APP, 'RespostaAnamnese')
        LinkAcesso = apps.get_model(APP, 'LinkAcesso')

        prof = Profissional.objects.create(nome='Prof R', slug='prof-r')
        cli = Cliente.objects.create(nome='Cli R', telefone='11922223333')
        Cliente.objects.create(nome='Cli R2', telefone='11922224444')
        proc = Procedimento.objects.create(nome='Proc R', duracao_minutos=30)
        ini = timezone.now() + timedelta(days=5)
        at = Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc,
                                        data_hora_inicio=ini, data_hora_fim=ini + timedelta(minutes=30))
        Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc,
                                   data_hora_inicio=ini + timedelta(hours=2), data_hora_fim=ini + timedelta(hours=3))
        com_link = Notificacao.objects.create(atendimento=at, tipo='NPS')
        self.com_link = com_link.pk
        self.sem_link = Notificacao.objects.create(atendimento=at, tipo='CANCELAMENTO').pk
        form = FormularioAnamnese.objects.create(nome='F', tipo='ANAMNESE', schema_json=[])
        for _ in range(3):
            RespostaAnamnese.objects.create(formulario=form, cliente=cli)
        LinkAcesso.objects.create(finalidade='NPS', notificacao=com_link, token_hash=_sha('tok-r-nps'))
        LinkAcesso.objects.create(finalidade='FEED_ICS', profissional=prof, token_hash=_sha('tok-r-ics'))

    def test_tokens_novos_unicos_e_links_antigos_param(self):
        Atendimento = self.apps.get_model(APP, 'Atendimento')
        Cliente = self.apps.get_model(APP, 'Cliente')
        Profissional = self.apps.get_model(APP, 'Profissional')
        Notificacao = self.apps.get_model(APP, 'Notificacao')
        RespostaAnamnese = self.apps.get_model(APP, 'RespostaAnamnese')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')

        for Model, campo in ((Atendimento, 'token_cancelamento'), (Cliente, 'token_descadastro'),
                             (Profissional, 'ics_token'), (RespostaAnamnese, 'token')):
            with self.subTest(campo=campo):
                tokens = list(Model._base_manager.values_list(campo, flat=True))
                self.assertTrue(tokens)
                self.assertNotIn(None, tokens)
                self.assertEqual(len(set(tokens)), len(tokens))
                self.assertTrue(all(len(t) >= 43 for t in tokens))
        self.assertIsNotNone(Notificacao.objects.get(pk=self.com_link).token)
        self.assertIsNone(Notificacao.objects.get(pk=self.sem_link).token)
        self.assertNotIn('tok-r-ics', Profissional.objects.values_list('ics_token', flat=True))
        self.assertTrue(LogAuditoria.objects.filter(acao__startswith='migration 0049 (reversa)').exists())
        self.assertNotIn('link_acesso', connection.introspection.table_names())


# ─── 0050: consentimentos com historico ────────────────────────────────────
M0050 = '0050_consentimento'
PARES_0050 = (
    ('email_marketing', 'MARKETING', 'EMAIL'),
    ('whatsapp_nps', 'PESQUISA_SATISFACAO', 'WHATSAPP'),
    ('whatsapp_confirmacao', 'LEMBRETE_AGENDAMENTO', 'WHATSAPP'),
)


def _estado_vigente(Consentimento, cliente_id):
    vigente = {}
    for c in Consentimento.objects.filter(cliente_id=cliente_id).order_by('-registrado_em', '-id'):
        vigente.setdefault((c.finalidade, c.canal), c.concedido)
    return vigente


@tag('migracoes')
class Consentimentos0050Tests(_MigracaoBase):
    """consent_*/_em/_ip + aceita_comunicacao -> historico append-only. Invariante:
    estado vigente = consent_x AND (aceita_comunicacao OR par = LEMBRETE)."""
    migrate_from = M0049
    migrate_to = M0050

    def preparar(self, apps):
        Cliente = apps.get_model(APP, 'Cliente')
        agora = timezone.now().replace(microsecond=0)
        self.agora = agora
        d = lambda dias: agora - timedelta(days=dias)  # noqa: E731
        self.casos = {}

        def cli(rotulo, tel, **campos):
            c = Cliente._base_manager.create(nome=rotulo, telefone=tel, **campos)
            self.casos[rotulo] = (c.pk, campos)
            return c

        # tudo concedido, com data/IP do aceite
        cli('todos', '11900000001', consent_email_marketing=True, consent_email_marketing_em=d(30),
            consent_email_marketing_ip='10.0.0.1', consent_whatsapp_nps=True, consent_whatsapp_nps_em=d(30),
            consent_whatsapp_confirmacao=True, consent_whatsapp_confirmacao_em=d(30),
            consent_whatsapp_confirmacao_ip='2001:db8::1')
        # revogado no wizard (False com data/IP da revogacao) e nunca concedido
        cli('revogou_nps', '11900000002', consent_whatsapp_nps=False, consent_whatsapp_nps_em=d(5),
            consent_whatsapp_nps_ip='10.0.0.2', consent_whatsapp_confirmacao=True,
            consent_whatsapp_confirmacao_em=d(40))
        # descadastrado: aceita_comunicacao=False com opt-ins ainda True (o descadastro antigo
        # nao mexia nos *_em) -> marketing/NPS vetados, lembrete continua
        descad = cli('descadastrado', '11900000003', aceita_comunicacao=False,
                     consent_email_marketing=True, consent_email_marketing_em=d(60),
                     consent_whatsapp_nps=True, consent_whatsapp_nps_em=d(60),
                     consent_whatsapp_confirmacao=True, consent_whatsapp_confirmacao_em=d(60))
        # cadastro/atualizacao ANTERIORES ao aceite (dado sujo): a revogacao ainda fica por ultimo
        Cliente._base_manager.filter(pk=descad.pk).update(atualizado_em=d(90), criado_em=d(100))
        # concedido sem data (legado): registrado_em = criado_em
        self.sem_data = cli('sem_data', '11900000004', consent_email_marketing=True).pk
        # opt-out geral sem nenhum opt-in
        cli('so_opt_out', '11900000005', aceita_comunicacao=False)
        # nada (nunca concedeu): sem linha
        self.nada = cli('nada', '11900000006').pk
        # excluido/anonimizado tambem migra (historico e prova)
        cli('excluido', '11900000007', deletado_em=d(1), ativo=False, consent_whatsapp_nps=True,
            consent_whatsapp_nps_em=d(100))

    def test_historico_migrado_preserva_o_estado(self):
        Consentimento = self.apps.get_model(APP, 'Consentimento')
        Cliente = self.apps.get_model(APP, 'Cliente')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        for rotulo, (pk, campos) in self.casos.items():
            aceita = campos.get('aceita_comunicacao', True)
            vigente = _estado_vigente(Consentimento, pk)
            for sufixo, fin, canal in PARES_0050:
                esperado = bool(campos.get(f'consent_{sufixo}')) and (aceita or fin == 'LEMBRETE_AGENDAMENTO')
                with self.subTest(cliente=rotulo, par=fin):
                    self.assertEqual(vigente.get((fin, canal), False), esperado)

        todos = Consentimento.objects.filter(cliente_id=self.casos['todos'][0])
        self.assertEqual(todos.count(), 3)
        mkt = todos.get(finalidade='MARKETING')
        self.assertEqual((mkt.concedido, mkt.origem, mkt.registrado_em, mkt.ip_origem),
                         (True, 'MIGRACAO', self.agora - timedelta(days=30), '10.0.0.1'))
        self.assertEqual(mkt.texto, '(migrado de cliente.consent_email_marketing)')
        self.assertEqual(todos.get(finalidade='LEMBRETE_AGENDAMENTO').ip_origem, '2001:db8::1')

        revogou = Consentimento.objects.get(cliente_id=self.casos['revogou_nps'][0], finalidade='PESQUISA_SATISFACAO')
        self.assertEqual((revogou.concedido, revogou.registrado_em, revogou.ip_origem),
                         (False, self.agora - timedelta(days=5), '10.0.0.2'))

        opt_out = Consentimento.objects.filter(cliente_id=self.casos['descadastrado'][0], concedido=False)
        self.assertEqual({c.finalidade for c in opt_out}, {'MARKETING', 'PESQUISA_SATISFACAO'})
        # greatest(atualizado_em, criado_em, consent_*_em) + 1 us: depois de qualquer concessao
        self.assertEqual({c.registrado_em for c in opt_out},
                         {self.agora - timedelta(days=60) + timedelta(microseconds=1)})
        self.assertEqual({c.texto for c in opt_out}, {'opt-out geral (aceita_comunicacao=false)'})

        sem_data = Consentimento.objects.get(cliente_id=self.sem_data)
        self.assertEqual(sem_data.registrado_em, Cliente._base_manager.get(pk=self.sem_data).criado_em)
        self.assertEqual(Consentimento.objects.filter(cliente_id=self.casos['so_opt_out'][0]).count(), 2)
        self.assertFalse(Consentimento.objects.filter(cliente_id=self.nada).exists())
        self.assertTrue(Consentimento.objects.filter(cliente_id=self.casos['excluido'][0], concedido=True).exists())

        log = LogAuditoria.objects.get(acao__startswith='migration 0050')
        self.assertEqual(log.detalhes['linhas'], Consentimento.objects.count())
        self.assertEqual(log.detalhes['opt_out_geral'], 2)
        with connection.cursor() as cursor:
            colunas = {c.name for c in connection.introspection.get_table_description(cursor, 'cliente')}
        self.assertFalse({c for c in colunas if c.startswith('consent_') or c == 'aceita_comunicacao'})

    def test_servico_le_o_historico_migrado(self):
        from aranha_estetica.models import Cliente
        from aranha_estetica.services import consentimentos

        _subir_ate_o_fim()
        descad = Cliente.all_objects.get(pk=self.casos['descadastrado'][0])
        self.assertEqual(consentimentos.estado_wizard(descad), {
            'consent_email_marketing': False, 'consent_whatsapp_nps': False, 'consent_whatsapp_confirmacao': True,
        })
        # reconsentimento depois da migracao volta a valer (o ultimo registro manda)
        consentimentos.registrar(descad, 'MARKETING', 'EMAIL', True, origem='AGENDAMENTO_ONLINE')
        self.assertTrue(consentimentos.vigente(descad, 'MARKETING', 'EMAIL'))


@tag('migracoes')
class ConsentimentosReversa0050Tests(_MigracaoBase):
    """Reverso (dev): colunas a partir da linha vigente; aceita_comunicacao=False so
    quando o opt-out geral (MIGRACAO/DESCADASTRO/ESQUECIMENTO) e o mais recente."""
    migrate_from = M0050
    migrate_to = M0049

    def preparar(self, apps):
        Cliente = apps.get_model(APP, 'Cliente')
        Consentimento = apps.get_model(APP, 'Consentimento')
        agora = timezone.now().replace(microsecond=0)
        self.agora = agora

        def linha(cli, fin, canal, concedido, dias, origem='AGENDAMENTO_ONLINE', ip=None, texto=''):
            Consentimento.objects.create(cliente=cli, finalidade=fin, canal=canal, concedido=concedido,
                                         origem=origem, registrado_em=agora - timedelta(days=dias),
                                         ip_origem=ip, texto=texto)

        self.ativa = Cliente.objects.create(nome='Ativa', telefone='11911110001')
        linha(self.ativa, 'MARKETING', 'EMAIL', True, 10, ip='10.9.9.9')
        linha(self.ativa, 'PESQUISA_SATISFACAO', 'WHATSAPP', True, 10)
        linha(self.ativa, 'PESQUISA_SATISFACAO', 'WHATSAPP', False, 5)   # revogou so o NPS (wizard)
        self.descad = Cliente.objects.create(nome='Descad', telefone='11911110002')
        linha(self.descad, 'MARKETING', 'EMAIL', True, 20)
        linha(self.descad, 'MARKETING', 'EMAIL', False, 3, origem='DESCADASTRO')
        linha(self.descad, 'LEMBRETE_AGENDAMENTO', 'WHATSAPP', True, 1)
        self.voltou = Cliente.objects.create(nome='Voltou', telefone='11911110003')
        linha(self.voltou, 'MARKETING', 'EMAIL', False, 9, origem='MIGRACAO',
              texto='opt-out geral (aceita_comunicacao=false)')
        linha(self.voltou, 'MARKETING', 'EMAIL', True, 2)
        self.sem = Cliente.objects.create(nome='Sem', telefone='11911110004')

    def test_colunas_restauradas(self):
        Cliente = self.apps.get_model(APP, 'Cliente')
        ativa = Cliente.objects.get(pk=self.ativa.pk)
        self.assertEqual((ativa.consent_email_marketing, ativa.consent_email_marketing_em,
                          ativa.consent_email_marketing_ip),
                         (True, self.agora - timedelta(days=10), '10.9.9.9'))
        self.assertEqual((ativa.consent_whatsapp_nps, ativa.consent_whatsapp_nps_em),
                         (False, self.agora - timedelta(days=5)))
        self.assertTrue(ativa.aceita_comunicacao)  # revogacao no wizard nao e opt-out geral
        descad = Cliente.objects.get(pk=self.descad.pk)
        self.assertEqual((descad.consent_email_marketing, descad.consent_whatsapp_confirmacao,
                          descad.aceita_comunicacao), (False, True, False))
        self.assertTrue(Cliente.objects.get(pk=self.voltou.pk).aceita_comunicacao)
        sem = Cliente.objects.get(pk=self.sem.pk)
        self.assertEqual((sem.consent_email_marketing, sem.consent_email_marketing_em, sem.aceita_comunicacao),
                         (False, None, True))
        self.assertNotIn('consentimento', connection.introspection.table_names())


# ─── 0051: questionarios unificados (formulario PRONTUARIO) ────────────────
M0051 = '0051_formulario_prontuario'
CONFIG_PRONTUARIO = [
    {'chave': 'esta_gravida_ou_amamentando', 'texto': 'Está grávida ou amamentando?', 'tipo': 'BOOLEAN'},
    {'chave': 'cirurgias', 'texto': 'Cirurgias anteriores', 'tipo': 'TEXTO'},
    {'chave': 'fuma', 'texto': 'Fuma?', 'tipo': 'BOOLEAN'},
]


@tag('migracoes')
class FormularioProntuario0051Tests(_MigracaoBase):
    """Configuracao.prontuario_perguntas -> formulario PRONTUARIO (key = chave); respostas
    intactas; alertas iguais; linha duplicada fica e vai p/ o log."""
    migrate_from = M0050
    migrate_to = M0051

    def preparar(self, apps):
        import json

        Configuracao = apps.get_model(APP, 'Configuracao')
        Cliente = apps.get_model(APP, 'Cliente')
        Prontuario = apps.get_model(APP, 'Prontuario')
        FormularioAnamnese = apps.get_model(APP, 'FormularioAnamnese')
        # a tela antiga forcava upper(); o leitor usava iexact + 1a por pk
        self.cfg = Configuracao.objects.create(chave='PRONTUARIO_PERGUNTAS', valor=json.dumps(CONFIG_PRONTUARIO))
        self.dup = Configuracao.objects.create(chave='prontuario_perguntas', valor='[]').pk
        cli = Cliente.objects.create(nome='Zelia', telefone='11933330001')
        self.cli = cli.pk
        self.extras = {'esta_gravida_ou_amamentando': True, 'cirurgias': 'Rinoplastia', 'fuma': False,
                       'pergunta_removida_x': 'historico'}
        Prontuario.objects.create(cliente=cli, respostas_extras=self.extras)
        self.anamnese = FormularioAnamnese.objects.create(nome='Ficha', tipo='ANAMNESE', schema_json=[]).pk

    def test_configuracao_vira_formulario(self):
        FormularioAnamnese = self.apps.get_model(APP, 'FormularioAnamnese')
        Configuracao = self.apps.get_model(APP, 'Configuracao')
        Prontuario = self.apps.get_model(APP, 'Prontuario')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')

        form = FormularioAnamnese.objects.get(tipo='PRONTUARIO')
        self.assertEqual((form.nome, form.escopo, form.ativo, form.obrigatorio),
                         ('Questionário do prontuário', 'GLOBAL', True, False))
        self.assertEqual(form.schema_json, [
            {'key': 'esta_gravida_ou_amamentando', 'label': 'Está grávida ou amamentando?', 'tipo': 'bool',
             'obrigatorio': False},
            {'key': 'cirurgias', 'label': 'Cirurgias anteriores', 'tipo': 'longtext', 'obrigatorio': False},
            {'key': 'fuma', 'label': 'Fuma?', 'tipo': 'bool', 'obrigatorio': False},
        ])
        self.assertFalse(Configuracao.objects.filter(pk=self.cfg.pk).exists())
        self.assertTrue(Configuracao.objects.filter(pk=self.dup).exists())
        self.assertEqual(Prontuario.objects.get(cliente_id=self.cli).respostas_extras, self.extras)
        self.assertEqual(FormularioAnamnese.objects.get(pk=self.anamnese).tipo, 'ANAMNESE')
        log = LogAuditoria.objects.get(acao__startswith='migration 0051')
        self.assertEqual(log.detalhes, {'configuracao_id': self.cfg.pk, 'configuracao_duplicada_ignorada': [self.dup],
                                        'formulario_id': form.pk, 'perguntas': 3})

    def test_alertas_e_ficha_continuam_iguais(self):
        from aranha_estetica.models import Cliente
        from aranha_estetica.utils.saude import alertas_saude, perguntas_prontuario

        _subir_ate_o_fim()
        alertas = alertas_saude(Cliente.objects.get(pk=self.cli))
        # mesmo resultado da regra antiga: label do texto da Configuracao; chave sem pergunta
        # so alerta se a chave casa a heuristica ('pergunta_removida_x' nao casa). A ordem
        # segue a do JSON devolvido pelo banco (o jsonb do PG ordena as chaves).
        self.assertEqual(sorted(alertas, key=lambda a: a['label']), [
            {'label': 'Cirurgias anteriores', 'valor': 'Rinoplastia', 'origem': 'prontuario'},
            {'label': 'Está grávida ou amamentando?', 'valor': 'Sim', 'origem': 'prontuario'},
        ])
        self.assertEqual([p['key'] for p in perguntas_prontuario()],
                         ['esta_gravida_ou_amamentando', 'cirurgias', 'fuma'])


@tag('migracoes')
class FormularioProntuarioJsonInvalido0051Tests(_MigracaoBase):
    migrate_from = M0050
    migrate_to = M0051

    def preparar(self, apps):
        Configuracao = apps.get_model(APP, 'Configuracao')
        self.cfg = Configuracao.objects.create(chave='prontuario_perguntas', valor='[{"texto": "sem chave"}]').pk

    def test_json_invalido_fica_na_configuracao_e_no_log(self):
        FormularioAnamnese = self.apps.get_model(APP, 'FormularioAnamnese')
        Configuracao = self.apps.get_model(APP, 'Configuracao')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        self.assertFalse(FormularioAnamnese.objects.filter(tipo='PRONTUARIO').exists())
        self.assertEqual(Configuracao.objects.get(pk=self.cfg).valor, '[{"texto": "sem chave"}]')
        log = LogAuditoria.objects.get(acao__startswith='migration 0051')
        self.assertIn('JSON invalido', log.acao)
        self.assertEqual(log.registro_id, self.cfg)


@tag('migracoes')
class FormularioProntuarioReversa0051Tests(_MigracaoBase):
    migrate_from = M0051
    migrate_to = M0050

    def preparar(self, apps):
        FormularioAnamnese = apps.get_model(APP, 'FormularioAnamnese')
        FormularioAnamnese.objects.create(nome='Antigo', tipo='PRONTUARIO', ativo=False, schema_json=[
            {'key': 'velha', 'label': 'Velha', 'tipo': 'bool'}])
        FormularioAnamnese.objects.create(nome='Questionário', tipo='PRONTUARIO', schema_json=[
            {'key': 'fuma', 'label': 'Fuma?', 'tipo': 'bool', 'obrigatorio': False},
            {'key': 'pele', 'label': 'Pele', 'tipo': 'select', 'opcoes': ['A'], 'obrigatorio': False},
        ])
        self.anamnese = FormularioAnamnese.objects.create(nome='Ficha', tipo='ANAMNESE', schema_json=[]).pk

    def test_formulario_volta_para_a_configuracao(self):
        import json

        FormularioAnamnese = self.apps.get_model(APP, 'FormularioAnamnese')
        Configuracao = self.apps.get_model(APP, 'Configuracao')
        self.assertEqual(json.loads(Configuracao.objects.get(chave='prontuario_perguntas').valor), [
            {'chave': 'fuma', 'texto': 'Fuma?', 'tipo': 'BOOLEAN'},
            {'chave': 'pele', 'texto': 'Pele', 'tipo': 'TEXTO'},
        ])
        self.assertEqual(list(FormularioAnamnese.objects.values_list('pk', flat=True)), [self.anamnese])


@tag('migracoes')
class FormularioProntuario0051AbortaTests(TransactionTestCase):
    """Pre-checagem: tipo/escopo fora do dominio ou escopo incoerente abortam a 0051
    (nada aplicado) com os ids na mensagem."""
    serialized_rollback = True

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([(APP, M0050)])
        self.antes = executor.loader.project_state([(APP, M0050)]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_escopo_incoerente_aborta(self):
        from django.db.migrations.recorder import MigrationRecorder

        Formulario = self.antes.get_model(APP, 'FormularioAnamnese')
        sem_proc = Formulario.objects.create(nome='A', escopo='PROCEDIMENTO', schema_json=[])
        sem_cat = Formulario.objects.create(nome='B', escopo='CATEGORIA', categoria='', schema_json=[])
        fora = Formulario.objects.create(nome='C', escopo='TUDO', schema_json=[])
        ok = Formulario.objects.create(nome='D', escopo='CATEGORIA', categoria='FACIAL', schema_json=[])
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        with self.assertRaises(RuntimeError) as ctx:
            executor.migrate([(APP, M0051)])
        msg = str(ctx.exception)
        self.assertIn('0051', msg)
        for f in (sem_proc, sem_cat, fora):
            self.assertIn(f'id={f.pk} ', msg)
        self.assertNotIn(f'id={ok.pk} ', msg)
        self.assertNotIn((APP, M0051), MigrationRecorder(connection).applied_migrations())
        with connection.cursor() as cursor:
            nomes = set(connection.introspection.get_constraints(cursor, 'formulario_anamnese'))
        self.assertNotIn('ck_formulario_tipo', nomes)
        Formulario.objects.filter(pk__in=[sem_proc.pk, sem_cat.pk, fora.pk]).delete()


# ─── 0052: pacote congelado na venda (compra_pacote_item) ──────────────────
M0052 = '0052_compra_pacote_item'


def _cenario_pacotes(apps, agora):
    """Banco 0051: compra com consumo; pacote cujo catalogo mudou depois da venda (item
    removido com consumo + quantidade reduzida abaixo do consumido); pacote sem itens."""
    Profissional = apps.get_model(APP, 'Profissional')
    Cliente = apps.get_model(APP, 'Cliente')
    Procedimento = apps.get_model(APP, 'Procedimento')
    Atendimento = apps.get_model(APP, 'Atendimento')
    Pacote = apps.get_model(APP, 'Pacote')
    ItemPacote = apps.get_model(APP, 'ItemPacote')
    CompraPacote = apps.get_model(APP, 'CompraPacote')
    ConsumoSessao = apps.get_model(APP, 'ConsumoSessao')

    prof = Profissional.objects.create(nome='Prof P')
    procs = {nome: Procedimento.objects.create(nome=nome, duracao_minutos=30)
             for nome in ('Drenagem', 'Peeling', 'Laser')}
    cli = Cliente.objects.create(nome='Paula P', telefone='11955550001')
    combo = Pacote.objects.create(nome='Combo', preco_total=Decimal('1000.00'))
    ItemPacote.objects.create(pacote=combo, procedimento=procs['Drenagem'], quantidade_sessoes=4)
    ItemPacote.objects.create(pacote=combo, procedimento=procs['Peeling'], quantidade_sessoes=2)
    antigo = Pacote.objects.create(nome='Antigo (editado)', preco_total=Decimal('500.00'))
    ItemPacote.objects.create(pacote=antigo, procedimento=procs['Peeling'], quantidade_sessoes=1)
    vazio = Pacote.objects.create(nome='Sem itens', preco_total=Decimal('90.00'))
    validade = agora.date() + timedelta(days=200)
    compras = {
        'combo': CompraPacote.objects.create(cliente=cli, pacote=combo, valor_pago=Decimal('1000.00'),
                                             data_expiracao=validade),
        'antigo': CompraPacote.objects.create(cliente=cli, pacote=antigo, valor_pago=Decimal('500.00'),
                                              data_expiracao=validade),
        'vazio': CompraPacote.objects.create(cliente=cli, pacote=vazio, valor_pago=Decimal('90.00'),
                                             data_expiracao=validade),
    }
    hora = [agora - timedelta(days=30)]

    def consumir(compra, proc):
        ini = hora[0]
        hora[0] += timedelta(hours=1)
        at = Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=procs[proc], status='REALIZADO',
                                        data_hora_inicio=ini, data_hora_fim=ini + timedelta(minutes=30))
        return ConsumoSessao.objects.create(compra_pacote=compras[compra], atendimento=at).pk

    consumos = {
        'combo_drenagem': [consumir('combo', 'Drenagem') for _ in range(2)],
        'combo_peeling': [consumir('combo', 'Peeling')],
        # o catalogo de "antigo" perdeu o Laser e baixou o Peeling p/ 1 depois das sessoes
        'antigo_laser': [consumir('antigo', 'Laser') for _ in range(2)],
        'antigo_peeling': [consumir('antigo', 'Peeling') for _ in range(3)],
    }
    return {'procs': {k: v.pk for k, v in procs.items()}, 'compras': {k: v.pk for k, v in compras.items()},
            'consumos': consumos, 'combo': combo.pk, 'cliente': cli.pk, 'prof': prof.pk}


@tag('migracoes')
class PacoteCongelado0052Tests(_MigracaoBase):
    """compra_pacote ganha pacote_nome + itens congelados; consumo aponta p/ o item do mesmo
    procedimento; consumo sem item -> item reconstruido (log); acima da quantidade -> log."""
    migrate_from = M0051
    migrate_to = M0052

    def preparar(self, apps):
        self.c = _cenario_pacotes(apps, timezone.now().replace(microsecond=0))

    def _itens(self, compra):
        CompraPacoteItem = self.apps.get_model(APP, 'CompraPacoteItem')
        return [(i.procedimento_id, i.procedimento_nome, i.quantidade_sessoes, i.valor_unitario)
                for i in CompraPacoteItem.objects.filter(compra_pacote_id=self.c['compras'][compra]).order_by('pk')]

    def test_snapshot_nome_itens_e_rateio(self):
        CompraPacote = self.apps.get_model(APP, 'CompraPacote')
        p = self.c['procs']
        nomes = dict(CompraPacote.objects.values_list('pk', 'pacote_nome'))
        self.assertEqual(nomes, {self.c['compras']['combo']: 'Combo', self.c['compras']['antigo']: 'Antigo (editado)',
                                 self.c['compras']['vazio']: 'Sem itens'})
        # 1000 / 6 sessoes = 166,67 (a formula da comissao de sessao de pacote)
        self.assertEqual(self._itens('combo'), [(p['Drenagem'], 'Drenagem', 4, Decimal('166.67')),
                                                (p['Peeling'], 'Peeling', 2, Decimal('166.67'))])
        # catalogo atual (Peeling 1x) + Laser reconstruido (2 consumos, valor 0)
        self.assertEqual(self._itens('antigo'), [(p['Peeling'], 'Peeling', 1, Decimal('500.00')),
                                                 (p['Laser'], 'Laser', 2, Decimal('0.00'))])
        self.assertEqual(self._itens('vazio'), [])

    def test_consumo_aponta_para_o_item_do_mesmo_procedimento(self):
        ConsumoSessao = self.apps.get_model(APP, 'ConsumoSessao')
        for chave, ids in self.c['consumos'].items():
            for consumo in ConsumoSessao.objects.filter(pk__in=ids).select_related('compra_pacote_item', 'atendimento'):
                with self.subTest(chave):
                    self.assertEqual(consumo.compra_pacote_item.compra_pacote_id, consumo.compra_pacote_id)
                    self.assertEqual(consumo.compra_pacote_item.procedimento_id, consumo.atendimento.procedimento_id)
        self.assertFalse(ConsumoSessao.objects.filter(compra_pacote_item__isnull=True).exists())

    def test_logs_de_reconstrucao_e_consumo_acima(self):
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        CompraPacoteItem = self.apps.get_model(APP, 'CompraPacoteItem')
        laser = CompraPacoteItem.objects.get(compra_pacote_id=self.c['compras']['antigo'],
                                             procedimento_id=self.c['procs']['Laser'])
        peeling = CompraPacoteItem.objects.get(compra_pacote_id=self.c['compras']['antigo'],
                                               procedimento_id=self.c['procs']['Peeling'])
        rec = LogAuditoria.objects.get(acao='migration 0052: item reconstruido')
        self.assertEqual((rec.tabela, rec.registro_id), ('compra_pacote_item', laser.pk))
        self.assertEqual((rec.detalhes['qtd_sessoes'], rec.detalhes['valor_unitario']), (2, '0.00'))
        acima = LogAuditoria.objects.get(acao='migration 0052: consumo acima da quantidade')
        self.assertEqual(acima.registro_id, peeling.pk)
        self.assertEqual((acima.detalhes['qtd_sessoes'], acima.detalhes['consumos']), (1, 3))
        self.assertEqual(acima.detalhes['consumo_ids'], self.c['consumos']['antigo_peeling'])
        resumo = LogAuditoria.objects.get(acao__startswith='migration 0052: pacote congelado')
        self.assertEqual(resumo.detalhes, {'compras': 3, 'itens': 4, 'consumos': 8,
                                           'itens_reconstruidos': 1, 'itens_com_consumo_acima': 1})

    def test_saldo_pelo_codigo_novo_e_catalogo_livre(self):
        from aranha_estetica.models import CompraPacote, ItemPacote

        _subir_ate_o_fim()
        combo = CompraPacote.objects.get(pk=self.c['compras']['combo'])
        self.assertEqual([(s['procedimento_nome'], s['total'], s['usadas'], s['restantes'])
                          for s in combo.saldo_por_procedimento()],
                         [('Drenagem', 4, 2, 2), ('Peeling', 2, 1, 1)])
        antigo = CompraPacote.objects.get(pk=self.c['compras']['antigo'])
        self.assertEqual([(s['procedimento_nome'], s['total'], s['usadas'], s['restantes'])
                          for s in antigo.saldo_por_procedimento()],
                         [('Peeling', 1, 3, 0), ('Laser', 2, 2, 0)])
        # o catalogo agora muda sem afetar a compra
        ItemPacote.objects.filter(pacote_id=self.c['combo']).delete()
        self.assertEqual(CompraPacote.objects.get(pk=combo.pk).sessoes_restantes(), 3)


@tag('migracoes')
class PacoteCongeladoBancoNovo0052Tests(_MigracaoBase):
    """Instalacao sem compras: nada a congelar, sem LogAuditoria."""
    migrate_from = M0051
    migrate_to = M0052

    def preparar(self, apps):
        pass

    def test_sem_log(self):
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        self.assertFalse(LogAuditoria.objects.filter(acao__startswith='migration 0052').exists())


@tag('migracoes')
class PacoteCongeladoReversa0052Tests(_MigracaoBase):
    """Reverso: itens e colunas saem; compras/consumos ficam (o catalogo continua la)."""
    migrate_from = M0052
    migrate_to = M0051

    def preparar(self, apps):
        CompraPacoteItem = apps.get_model(APP, 'CompraPacoteItem')
        ConsumoSessao = apps.get_model(APP, 'ConsumoSessao')
        Atendimento = apps.get_model(APP, 'Atendimento')
        Profissional = apps.get_model(APP, 'Profissional')
        Cliente = apps.get_model(APP, 'Cliente')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Pacote = apps.get_model(APP, 'Pacote')
        CompraPacote = apps.get_model(APP, 'CompraPacote')
        agora = timezone.now()
        proc = Procedimento.objects.create(nome='Drenagem', duracao_minutos=30)
        cli = Cliente.objects.create(nome='Rev', telefone='11955550009')
        pacote = Pacote.objects.create(nome='P', preco_total=Decimal('100.00'))
        compra = CompraPacote.objects.create(cliente=cli, pacote=pacote, pacote_nome='P', valor_pago=Decimal('100.00'))
        item = CompraPacoteItem.objects.create(compra_pacote=compra, procedimento=proc, procedimento_nome='Drenagem',
                                               quantidade_sessoes=2, valor_unitario=Decimal('50.00'))
        at = Atendimento.objects.create(cliente=cli, profissional=Profissional.objects.create(nome='X'),
                                        procedimento=proc, status='REALIZADO', data_hora_inicio=agora,
                                        data_hora_fim=agora + timedelta(minutes=30))
        self.consumo = ConsumoSessao.objects.create(compra_pacote=compra, compra_pacote_item=item, atendimento=at).pk
        self.compra = compra.pk

    def test_colunas_e_tabela_saem_dados_ficam(self):
        ConsumoSessao = self.apps.get_model(APP, 'ConsumoSessao')
        self.assertEqual(ConsumoSessao.objects.get(pk=self.consumo).compra_pacote_id, self.compra)
        with connection.cursor() as cursor:
            tabelas = connection.introspection.table_names(cursor)
            colunas_compra = {c.name for c in connection.introspection.get_table_description(cursor, 'compra_pacote')}
            colunas_consumo = {c.name for c in connection.introspection.get_table_description(cursor, 'consumo_sessao')}
        self.assertNotIn('compra_pacote_item', tabelas)
        self.assertNotIn('pacote_nome', colunas_compra)
        self.assertNotIn('compra_pacote_item_id', colunas_consumo)


# ─── 0053: comissao como livro-razao + nome fora do ledger da carteira ─────
M0053 = '0053_comissao_ledger'


def _cenario_comissoes(apps, agora):
    """Banco 0052: comissoes nos 3 status (e o PAGA sem pago_em sujo) + cashback com nome."""
    Profissional = apps.get_model(APP, 'Profissional')
    Cliente = apps.get_model(APP, 'Cliente')
    Procedimento = apps.get_model(APP, 'Procedimento')
    Atendimento = apps.get_model(APP, 'Atendimento')
    MovimentoComissao = apps.get_model(APP, 'MovimentoComissao')
    RegraComissao = apps.get_model(APP, 'RegraComissao')

    prof = Profissional.objects.create(nome='Prof C')
    proc = Procedimento.objects.create(nome='Botox', duracao_minutos=30)
    cli = Cliente.objects.create(nome='Cli C', telefone='11944440001')
    regra = RegraComissao.objects.create(percentual=Decimal('10'))
    hora = [agora - timedelta(days=40)]
    casos = {}

    def comissao(rotulo, status, valor, pago_em=None, atualizado=None):
        ini = hora[0]
        hora[0] += timedelta(hours=1)
        at = Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc, status='REALIZADO',
                                        data_hora_inicio=ini, data_hora_fim=ini + timedelta(minutes=30))
        mov = MovimentoComissao.objects.create(profissional=prof, atendimento=at, regra=regra, valor=Decimal(valor),
                                               status=status, pago_em=pago_em)
        # auto_now_add nas duas tabelas: datas de "producao" por update (nao ha trigger na 0052)
        MovimentoComissao.objects.filter(pk=mov.pk).update(criado_em=ini + timedelta(hours=1))
        Atendimento.objects.filter(pk=at.pk).update(atualizado_em=atualizado)
        casos[rotulo] = {'id': mov.pk, 'atendimento': at.pk, 'criado_em': ini + timedelta(hours=1),
                         'pago_em': pago_em, 'atualizado': atualizado, 'valor': Decimal(valor), 'status': status}

    comissao('pendente', 'PENDENTE', '10.00')
    comissao('paga', 'PAGA', '20.00', pago_em=agora - timedelta(days=20))
    comissao('estornada_paga', 'ESTORNADA', '40.00', pago_em=agora - timedelta(days=15),
             atualizado=agora - timedelta(days=10))
    comissao('estornada', 'ESTORNADA', '80.00', atualizado=agora - timedelta(days=5))
    comissao('estornada_sem_data', 'ESTORNADA', '5.00')            # atendimento.atualizado_em NULL
    comissao('paga_sem_data', 'PAGA', '7.00')                      # dado sujo: PAGA sem pago_em
    return {'casos': casos, 'prof': prof.pk, 'cliente': cli.pk}


def _cenario_cashback(apps):
    Cliente = apps.get_model(APP, 'Cliente')
    Carteira = apps.get_model(APP, 'Carteira')
    MovimentoCarteira = apps.get_model(APP, 'MovimentoCarteira')
    indicadora = Cliente.objects.create(nome='Ana Indicadora', telefone='11944449001')
    carteira = Carteira.objects.create(cliente=indicadora, saldo=Decimal('30.00'))
    nomes = ('Beatriz (Bia) Sigilosa', 'Carla\nQuebra de linha')
    ids = []
    for i, nome in enumerate(nomes):
        ids.append(MovimentoCarteira.objects.create(
            carteira=carteira, tipo='CREDITO', origem='CASHBACK_INDICACAO', valor=Decimal('10.00'),
            saldo_resultante=Decimal('10.00') * (i + 1), observacoes=f'Indicacao: {nome} (cli #{900 + i})',
        ).pk)
    outro = MovimentoCarteira.objects.create(
        carteira=carteira, tipo='CREDITO', origem='AJUSTE_MANUAL', valor=Decimal('10.00'),
        saldo_resultante=Decimal('30.00'), observacoes='Indicacao: ajuste manual (cli #1)',
    ).pk
    return {'cashback': ids, 'outro': outro, 'nomes': nomes}


@tag('migracoes')
class ComissaoLedger0053Tests(_MigracaoBase):
    """status -> lancamentos: PENDENTE = CREDITO; PAGA = + PAGAMENTO(pago_em); ESTORNADA = + ESTORNO
    (data estimada; + PAGAMENTO se tinha pago_em). Status derivado = status antigo."""
    migrate_from = M0052
    migrate_to = M0053

    def preparar(self, apps):
        self.agora = timezone.now().replace(microsecond=0)
        self.c = _cenario_comissoes(apps, self.agora)
        self.cb = _cenario_cashback(apps)

    def _lancamentos(self, rotulo):
        MovimentoComissao = self.apps.get_model(APP, 'MovimentoComissao')
        caso = self.c['casos'][rotulo]
        return {m.tipo: m for m in MovimentoComissao.objects.filter(atendimento_id=caso['atendimento'])}

    def test_cada_status_vira_os_lancamentos_certos(self):
        tipos = {rotulo: sorted(self._lancamentos(rotulo)) for rotulo in self.c['casos']}
        self.assertEqual(tipos, {
            'pendente': ['CREDITO'],
            'paga': ['CREDITO', 'PAGAMENTO'],
            'estornada_paga': ['CREDITO', 'ESTORNO', 'PAGAMENTO'],
            'estornada': ['CREDITO', 'ESTORNO'],
            'estornada_sem_data': ['CREDITO', 'ESTORNO'],
            'paga_sem_data': ['CREDITO', 'PAGAMENTO'],
        })
        for rotulo, caso in self.c['casos'].items():
            lanc = self._lancamentos(rotulo)
            credito = lanc.pop('CREDITO')
            with self.subTest(rotulo):
                self.assertEqual((credito.pk, credito.valor, credito.movimento_origem_id), (caso['id'], caso['valor'], None))
                self.assertIsNotNone(credito.regra_id)
                for derivado in lanc.values():
                    self.assertEqual((derivado.movimento_origem_id, derivado.valor, derivado.profissional_id,
                                      derivado.regra_id, derivado.usuario_autor_id),
                                     (caso['id'], caso['valor'], self.c['prof'], None, None))

    def test_datas_reais_e_estimadas(self):
        casos = self.c['casos']
        paga = self._lancamentos('paga')['PAGAMENTO']
        self.assertEqual((paga.criado_em, paga.observacoes), (casos['paga']['pago_em'], 'migração 0053'))
        ep = self._lancamentos('estornada_paga')
        self.assertEqual(ep['PAGAMENTO'].criado_em, casos['estornada_paga']['pago_em'])
        self.assertEqual((ep['ESTORNO'].criado_em, ep['ESTORNO'].observacoes),
                         (casos['estornada_paga']['atualizado'], 'migração 0053: data estimada'))
        self.assertEqual(self._lancamentos('estornada')['ESTORNO'].criado_em, casos['estornada']['atualizado'])
        # sem atendimento.atualizado_em: a data do proprio credito
        self.assertEqual(self._lancamentos('estornada_sem_data')['ESTORNO'].criado_em,
                         casos['estornada_sem_data']['criado_em'])
        psd = self._lancamentos('paga_sem_data')['PAGAMENTO']
        self.assertEqual((psd.criado_em, psd.observacoes),
                         (casos['paga_sem_data']['criado_em'], 'migração 0053: data estimada'))

    def test_status_e_saldo_derivados_pelo_codigo_novo(self):
        from aranha_estetica.models import MovimentoComissao
        from aranha_estetica.services.comissao import ComissaoService

        _subir_ate_o_fim()
        derivado = dict(MovimentoComissao.objects.creditos_com_status().values_list('pk', 'status'))
        self.assertEqual(derivado, {c['id']: c['status'] for c in self.c['casos'].values()})
        pago_em = dict(MovimentoComissao.objects.creditos_com_status().values_list('pk', 'pago_em'))
        self.assertEqual(pago_em[self.c['casos']['paga']['id']], self.c['casos']['paga']['pago_em'])
        # so o pago-e-estornado fica devendo: 10 (pendente) − 40 = −30
        self.assertEqual(ComissaoService.saldo_a_pagar(self.c['prof']), Decimal('-30.00'))

    def test_log_e_colunas(self):
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        log = LogAuditoria.objects.get(acao__startswith='migration 0053: comissao')
        self.assertEqual(log.detalhes, {'creditos': 6, 'pendentes': 1, 'pagas': 2, 'estornadas': 3,
                                        'pagamentos': 3, 'estornos': 3, 'datas_estimadas': 4,
                                        'estornadas_depois_de_pagas': 1})
        with connection.cursor() as cursor:
            colunas = {c.name for c in connection.introspection.get_table_description(cursor, 'movimento_comissao')}
            nomes = set(connection.introspection.get_constraints(cursor, 'movimento_comissao'))
        self.assertTrue({'tipo', 'movimento_comissao_origem_id', 'usuario_autor_id'} <= colunas)
        self.assertFalse({'status', 'pago_em'} & colunas)
        self.assertNotIn('uniq_comissao_ativa_por_atendimento', nomes)
        self.assertTrue({'uq_movimento_comissao_credito_por_atendimento', 'ck_movimento_comissao_origem_coerente',
                         'ix_movimento_comissao_profissional_criado_em'} <= nomes)

    def test_nome_da_indicada_sai_do_ledger_da_carteira(self):
        MovimentoCarteira = self.apps.get_model(APP, 'MovimentoCarteira')
        LogAuditoria = self.apps.get_model(APP, 'LogAuditoria')
        textos = dict(MovimentoCarteira.objects.values_list('pk', 'observacoes'))
        self.assertEqual([textos[pk] for pk in self.cb['cashback']], ['Indicacao (cli #900)', 'Indicacao (cli #901)'])
        self.assertEqual(textos[self.cb['outro']], 'Indicacao: ajuste manual (cli #1)')  # outra origem: intocada
        log = LogAuditoria.objects.get(acao__startswith='migration 0053: nome da indicada')
        self.assertEqual(log.detalhes, {'movimentos': 2, 'ids': self.cb['cashback']})
        self.assertNotIn('Beatriz', str(log.detalhes))


@tag('migracoes')
class ComissaoLedgerReversa0053Tests(_MigracaoBase):
    """Reverso: status/pago_em voltam do ledger (ESTORNADA > PAGA > PENDENTE); so os CREDITOs ficam."""
    migrate_from = M0053
    migrate_to = M0052

    def preparar(self, apps):
        Profissional = apps.get_model(APP, 'Profissional')
        Cliente = apps.get_model(APP, 'Cliente')
        Procedimento = apps.get_model(APP, 'Procedimento')
        Atendimento = apps.get_model(APP, 'Atendimento')
        MovimentoComissao = apps.get_model(APP, 'MovimentoComissao')
        prof = Profissional.objects.create(nome='Prof R')
        proc = Procedimento.objects.create(nome='Botox', duracao_minutos=30)
        cli = Cliente.objects.create(nome='Cli R', telefone='11944440002')
        agora = timezone.now().replace(microsecond=0)
        self.esperado = {}
        for i, tipos in enumerate(((), ('PAGAMENTO',), ('ESTORNO',), ('PAGAMENTO', 'ESTORNO'))):
            ini = agora - timedelta(days=10 - i)
            at = Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc, status='REALIZADO',
                                            data_hora_inicio=ini, data_hora_fim=ini + timedelta(minutes=30))
            credito = MovimentoComissao.objects.create(tipo='CREDITO', profissional=prof, atendimento=at,
                                                       valor=Decimal('10.00'), criado_em=ini)
            pago_em = None
            for j, tipo in enumerate(tipos, start=1):
                quando = ini + timedelta(hours=j)
                MovimentoComissao.objects.create(tipo=tipo, profissional=prof, atendimento=at, valor=Decimal('10.00'),
                                                 movimento_origem=credito, criado_em=quando)
                pago_em = quando if tipo == 'PAGAMENTO' else pago_em
            status = 'ESTORNADA' if 'ESTORNO' in tipos else 'PAGA' if tipos else 'PENDENTE'
            self.esperado[credito.pk] = (status, pago_em)

    def test_status_volta_do_ledger(self):
        MovimentoComissao = self.apps.get_model(APP, 'MovimentoComissao')
        self.assertEqual({m.pk: (m.status, m.pago_em) for m in MovimentoComissao.objects.all()}, self.esperado)
        with connection.cursor() as cursor:
            colunas = {c.name for c in connection.introspection.get_table_description(cursor, 'movimento_comissao')}
        self.assertFalse({'tipo', 'movimento_comissao_origem_id', 'usuario_autor_id'} & colunas)


@tag('migracoes')
class ComissaoLedger0053AbortaTests(TransactionTestCase):
    """Pre-checagem: valor <= 0, 2 comissoes do mesmo atendimento ou status fora do dominio
    abortam a 0053 (nada aplicado) com os ids na mensagem."""
    serialized_rollback = True

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([(APP, M0052)])
        self.antes = executor.loader.project_state([(APP, M0052)]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_dado_invalido_aborta(self):
        from django.db.migrations.recorder import MigrationRecorder

        c = _cenario_comissoes(self.antes, timezone.now().replace(microsecond=0))
        MovimentoComissao = self.antes.get_model(APP, 'MovimentoComissao')
        ok = c['casos']['pendente']['id']
        dobra = c['casos']['paga']
        zero = MovimentoComissao.objects.create(profissional_id=c['prof'], atendimento_id=c['casos']['estornada']['atendimento'],
                                                valor=Decimal('0.00'), status='ESTORNADA')
        # o UNIQUE antigo so cobria PENDENTE/PAGA: a duplicata possivel e com ESTORNADA
        repetida = MovimentoComissao.objects.create(profissional_id=c['prof'], atendimento_id=dobra['atendimento'],
                                                    valor=Decimal('3.00'), status='ESTORNADA')
        fora = MovimentoComissao.objects.get(pk=c['casos']['paga_sem_data']['id'])
        MovimentoComissao.objects.filter(pk=fora.pk).update(status='CANCELADA')
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        with self.assertRaises(RuntimeError) as ctx:
            executor.migrate([(APP, M0053)])
        msg = str(ctx.exception)
        self.assertIn('0053', msg)
        for pk in (zero.pk, repetida.pk, dobra['id'], fora.pk):
            self.assertIn(f'id={pk} ', msg)
        self.assertIn('valor=0.00', msg)
        self.assertIn("status='CANCELADA'", msg)
        self.assertNotIn(f'id={ok} ', msg)
        self.assertNotIn((APP, M0053), MigrationRecorder(connection).applied_migrations())
        with connection.cursor() as cursor:
            colunas = {d.name for d in connection.introspection.get_table_description(cursor, 'movimento_comissao')}
        self.assertNotIn('tipo', colunas)
        MovimentoComissao.objects.filter(pk__in=[zero.pk, repetida.pk]).delete()
        MovimentoComissao.objects.filter(pk=fora.pk).update(status='PAGA')


# ─── 0060: dado de saude e token OAuth cifrados em repouso ─────────────
M0059 = '0059_trilha_append_only'
M0060 = '0060_cifragem_dados_saude'


def _cru(tabela, coluna, pk):
    import json
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {coluna} FROM {tabela} WHERE id = %s', [pk])
        valor = cursor.fetchone()[0]
    if isinstance(valor, str) and valor and valor[0] in '{[':
        return json.loads(valor)
    return valor


def _historico_prontuario(HistoricoAlteracao, pk):
    return HistoricoAlteracao.objects.create(
        tabela='prontuario', registro_pk=pk, operacao='UPDATE', colunas_alteradas=['alergias', 'respostas'],
        dados_antes={'alergias': '', 'respostas': {}},
        dados_depois={'alergias': 'Dipirona', 'respostas': {'fuma': True}}, transacao_ref=1,
    ).pk


@tag('migracoes')
class Cifragem0060Tests(_MigracaoBase):
    """0060: o que ja existe em claro (inclusive a trilha do prontuario) passa a ser gravado cifrado."""
    migrate_from = M0059
    migrate_to = M0060

    def preparar(self, apps):
        Cliente = apps.get_model(APP, 'Cliente')
        Prontuario = apps.get_model(APP, 'Prontuario')
        ProntuarioVersao = apps.get_model(APP, 'ProntuarioVersao')
        Profissional = apps.get_model(APP, 'Profissional')
        HistoricoAlteracao = apps.get_model(APP, 'HistoricoAlteracao')
        cli = Cliente.objects.create(nome='Cifra', telefone='11977770001')
        pront = Prontuario.objects.create(cliente=cli, alergias='Dipirona', respostas_extras={'fuma': True})
        vazio = Prontuario.objects.create(cliente=Cliente.objects.create(nome='Vazio', telefone='11977770002'))
        self.ids = {
            'pront': pront.pk, 'vazio': vazio.pk,
            'versao': ProntuarioVersao.objects.create(prontuario=pront, dados={'alergias': 'Latex'}).pk,
            'prof': Profissional.objects.create(nome='Dra Cifra', gcal_refresh_token='1//tok').pk,
            'trilha': _historico_prontuario(HistoricoAlteracao, pront.pk),
        }

    def test_dados_existentes_ficam_cifrados(self):
        from aranha_estetica.utils import cifragem

        alergias = _cru('prontuario', 'alergias', self.ids['pront'])
        self.assertTrue(alergias.startswith('cif1:'))
        self.assertEqual(cifragem.decifrar(alergias), 'Dipirona')
        self.assertEqual(cifragem.decifrar_json(_cru('prontuario', 'respostas', self.ids['pront'])), {'fuma': True})
        self.assertEqual(_cru('prontuario', 'respostas', self.ids['vazio']), {})
        self.assertEqual(_cru('prontuario', 'alergias', self.ids['vazio']), '')
        self.assertEqual(cifragem.decifrar_json(_cru('prontuario_versao', 'dados', self.ids['versao'])),
                         {'alergias': 'Latex'})
        self.assertEqual(cifragem.decifrar(_cru('profissional', 'gcal_token_renovacao', self.ids['prof']), 'segredos'),
                         '1//tok')
        depois = _cru('historico_alteracao', 'dados_depois', self.ids['trilha'])
        self.assertNotIn('Dipirona', str(depois))
        self.assertEqual(cifragem.decifrar_profundo(depois), {'alergias': 'Dipirona', 'respostas': {'fuma': True}})
        self.assertEqual(_cru('historico_alteracao', 'dados_antes', self.ids['trilha']), {'alergias': '', 'respostas': {}})
        # o model (estado 0060) le em claro
        Prontuario = self.apps.get_model(APP, 'Prontuario')
        self.assertEqual(Prontuario.objects.get(pk=self.ids['pront']).alergias, 'Dipirona')


@tag('migracoes')
class CifragemReversa0060Tests(_MigracaoBase):
    migrate_from = M0060
    migrate_to = M0059

    def preparar(self, apps):
        Cliente = apps.get_model(APP, 'Cliente')
        Prontuario = apps.get_model(APP, 'Prontuario')
        cli = Cliente.objects.create(nome='Rev Cifra', telefone='11977770003')
        self.pk = Prontuario.objects.create(cliente=cli, alergias='Latex', respostas_extras={'x': 1}).pk
        self.assertTrue(_cru('prontuario', 'alergias', self.pk).startswith('cif1:'))

    def test_reverso_decifra(self):
        self.assertEqual(_cru('prontuario', 'alergias', self.pk), 'Latex')
        self.assertEqual(_cru('prontuario', 'respostas', self.pk), {'x': 1})


@tag('migracoes')
class HabilitacaoObrigatoria0062Tests(_MigracaoBase):
    """0062: procedimento ativo sem habilitacao recebe a de todos os profissionais ativos (o mesmo
    conjunto que o atendia pela regra antiga); quem ja tinha habilitacao nao muda."""
    migrate_from = '0061_fila_email_notificacao'
    migrate_to = '0062_habilitacao_obrigatoria'

    def preparar(self, apps):
        Procedimento = apps.get_model(APP, 'Procedimento')
        Profissional = apps.get_model(APP, 'Profissional')
        Habilitacao = apps.get_model(APP, 'Habilitacao')
        self.ana = Profissional.objects.create(nome='Ana 0062', slug='ana-0062').pk
        self.bia = Profissional.objects.create(nome='Bia 0062', slug='bia-0062').pk
        self.inativa = Profissional.objects.create(nome='Cris 0062', slug='cris-0062', ativo=False).pk
        self.aberto = Procedimento.objects.create(nome='Drenagem 0062', slug='drenagem-0062', duracao_minutos=60).pk
        self.restrito = Procedimento.objects.create(nome='Toxina 0062', slug='toxina-0062', duracao_minutos=30).pk
        self.inativo = Procedimento.objects.create(nome='Antigo 0062', slug='antigo-0062', duracao_minutos=30, ativo=False).pk
        Habilitacao.objects.create(procedimento_id=self.restrito, profissional_id=self.ana)

    def test_sem_habilitacao_vira_todos_os_ativos(self):
        Habilitacao = self.apps.get_model(APP, 'Habilitacao')

        def quem(proc):
            return set(Habilitacao.objects.filter(procedimento_id=proc).values_list('profissional_id', flat=True))
        self.assertTrue({self.ana, self.bia} <= quem(self.aberto))
        self.assertNotIn(self.inativa, quem(self.aberto))
        self.assertEqual(quem(self.restrito), {self.ana})
        self.assertEqual(quem(self.inativo), set())
