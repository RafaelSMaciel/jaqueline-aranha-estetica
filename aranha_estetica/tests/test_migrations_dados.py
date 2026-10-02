"""Regressao das data migrations da remodelagem (0029, 0034-0037, 0042-0048).

Roda a migration real sobre dados "sujos" plausiveis do banco legado (SQLite:
as partes PG-only — CHECK regex, EXCLUDE, trigger, collation — sao validadas
no ensaio em Postgres; aqui fica a logica de dados, que e portavel).
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
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

APP = 'aranha_estetica'


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
        hoje = date.today()
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

        # a ficha legada passa a gerar alerta de saude (utils/saude)
        from aranha_estetica.models import Cliente
        from aranha_estetica.utils.saude import alertas_saude
        alertas = alertas_saude(Cliente.objects.get(pk=self.cliente_pk))
        self.assertIn('Dipirona', [a['valor'] for a in alertas])


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

        hoje = date.today()
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
