"""Regressao das data migrations da remodelagem (0034-0037, 0042, 0043).

Roda a migration real sobre dados "sujos" plausiveis do banco legado (SQLite:
as partes PG-only — CHECK regex, EXCLUDE, trigger, collation — sao validadas
no ensaio em Postgres; aqui fica a logica de dados, que e portavel).
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
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
                                                 data_inicio=hoje, data_fim=hoje + timedelta(days=5))
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
        self.assertEqual(Promocao.objects.get(pk=self.promo_150.pk).desconto_percentual, Decimal('100'))
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

    def preparar(self, apps):
        Usuario = apps.get_model(APP, 'Usuario')
        self.demo = Usuario.objects.create(email='admin@shivazen.com', nome='Admin',
                                           password=make_password('admin123'), papel='ADMIN')
        self.trocada = Usuario.objects.create(email='ana@shivazen.com', nome='Ana',
                                              password=make_password('senha-trocada-forte'),
                                              papel='PROFISSIONAL')

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

        Atendimento = self.apps.get_model(APP, 'Atendimento')
        self.assertEqual(Atendimento.objects.get(pk=self.ret_dup.pk).status, 'CANCELADO')
        self.assertEqual(Atendimento.objects.get(pk=self.ret_ok.pk).status, 'AGENDADO')
