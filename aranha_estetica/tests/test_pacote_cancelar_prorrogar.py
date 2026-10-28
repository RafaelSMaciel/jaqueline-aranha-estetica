"""Cancelamento e prorrogacao da compra de pacote (services/pacotes) e o catalogo pelo PacoteForm.

Cancelar: reembolso em dinheiro de 0 ao valor pago, motivo obrigatorio, cancelado_em gravado (o
reembolso deduz a receita do mes do cancelamento), so compra ATIVA. Prorrogar: so ADMIN (painel),
motivo obrigatorio, data depois da atual; EXPIRADO volta a ATIVO; trilha de/para.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.excecoes import RegraDeNegocio
from aranha_estetica.models import CompraPacote, ConsumoSessao, ItemPacote, LogAuditoria, Pacote, Usuario
from aranha_estetica.services import pacotes as pacotes_service

from .factories import criar_atendimento, criar_cliente, criar_compra_pacote, criar_pacote, criar_procedimento, \
    criar_profissional


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class _Base(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_user(email='adm@pacote.test', password='x', papel=Usuario.PAPEL_ADMIN)
        self.client.force_login(self.admin)
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente('Paula Pacote')
        self.pacote = criar_pacote('Drenagem 4x', preco=Decimal('800.00'), procedimento=self.proc, sessoes=4)
        self.compra = criar_compra_pacote(self.cli, self.pacote)


class CancelarCompraTests(_Base):
    def test_reembolso_sugerido_e_sessoes_restantes_vezes_unitario(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, data_hora=timezone.now() - timedelta(days=1))
        ConsumoSessao.objects.create(compra_pacote=self.compra, atendimento=at)
        self.assertEqual(pacotes_service.reembolso_sugerido(self.compra), Decimal('600.00'))   # 3 x 200
        resp = self.client.get(reverse('aranha:admin_cliente_detalhe', args=[self.cli.pk]))
        self.assertContains(resp, 'value="600.00"')

    def test_cancelar_grava_instante_reembolso_e_auditoria(self):
        url = reverse('aranha:admin_cancelar_compra_pacote', args=[self.compra.pk])
        resp = self.client.post(url, {'valor_reembolsado': '300,00', 'observacao': 'Mudou de cidade'})
        self.assertEqual(resp.status_code, 302)
        self.compra.refresh_from_db()
        self.assertEqual((self.compra.status, self.compra.valor_reembolsado), ('CANCELADO', Decimal('300.00')))
        self.assertIsNotNone(self.compra.cancelado_em)
        log = LogAuditoria.objects.get(tabela='compra_pacote', acao__startswith='Cancelou')
        self.assertEqual((log.detalhes['observacao'], log.detalhes['valor_reembolsado']), ('Mudou de cidade', '300.00'))

    def test_reembolso_maior_que_o_pago_ou_sem_motivo_e_recusado(self):
        url = reverse('aranha:admin_cancelar_compra_pacote', args=[self.compra.pk])
        for dados in ({'valor_reembolsado': '800,01', 'observacao': 'x'},
                      {'valor_reembolsado': '10', 'observacao': '   '},
                      {'valor_reembolsado': '-1', 'observacao': 'x'}):
            with self.subTest(dados=dados):
                resp = self.client.post(url, dados)
                self.assertEqual(resp.status_code, 302)
                self.assertTrue(_mensagens(resp))
                self.compra.refresh_from_db()
                self.assertEqual(self.compra.status, 'ATIVO')
        with self.assertRaises(RegraDeNegocio):
            pacotes_service.cancelar_compra(self.compra, Decimal('900.00'), motivo='x')

    def test_compra_nao_ativa_nao_cancela_de_novo(self):
        pacotes_service.cancelar_compra(self.compra, Decimal('0.00'), motivo='Desistiu')
        with self.assertRaises(RegraDeNegocio) as erro:
            pacotes_service.cancelar_compra(self.compra, Decimal('0.00'), motivo='De novo')
        self.assertEqual(erro.exception.codigo, 'compra_nao_ativa')
        self.assertEqual(LogAuditoria.objects.filter(acao__startswith='Cancelou').count(), 1)

    def test_get_nao_cancela(self):
        resp = self.client.get(reverse('aranha:admin_cancelar_compra_pacote', args=[self.compra.pk]))
        self.assertEqual(resp.status_code, 405)


class ProrrogarValidadeTests(_Base):
    def _post(self, nova_data, motivo='Cliente operada, sem poder vir', usuario=None):
        if usuario is not None:
            self.client.force_login(usuario)
        return self.client.post(reverse('aranha:admin_prorrogar_compra_pacote', args=[self.compra.pk]),
                                {'nova_data': nova_data.isoformat(), 'motivo': motivo})

    def test_prorroga_ativo_com_trilha_de_para(self):
        antes = self.compra.data_expiracao
        nova = antes + timedelta(days=60)
        self._post(nova)
        self.compra.refresh_from_db()
        self.assertEqual((self.compra.data_expiracao, self.compra.status), (nova, 'ATIVO'))
        log = LogAuditoria.objects.get(acao__startswith='Prorrogou')
        self.assertEqual(log.detalhes['de'], {'data_expiracao': antes.isoformat(), 'status': 'ATIVO'})
        self.assertEqual(log.detalhes['para'], {'data_expiracao': nova.isoformat(), 'status': 'ATIVO'})
        self.assertEqual(log.detalhes['motivo'], 'Cliente operada, sem poder vir')

    def test_expirado_prorrogado_volta_a_ativo(self):
        ontem = timezone.localdate() - timedelta(days=1)
        CompraPacote.objects.filter(pk=self.compra.pk).update(status='EXPIRADO', data_expiracao=ontem)
        self._post(timezone.localdate())          # hoje ainda vale (o ultimo dia e valido)
        self.compra.refresh_from_db()
        self.assertEqual((self.compra.status, self.compra.data_expiracao), ('ATIVO', timezone.localdate()))

    def test_recusas(self):
        atual = self.compra.data_expiracao
        casos = (
            (atual, 'x', 'validade_nao_posterior'),
            (atual + timedelta(days=1), '  ', 'motivo_obrigatorio'),
        )
        for data, motivo, codigo in casos:
            with self.subTest(codigo=codigo), self.assertRaises(RegraDeNegocio) as erro:
                pacotes_service.prorrogar_validade(self.compra, data, motivo=motivo)
            self.assertEqual(erro.exception.codigo, codigo)
        CompraPacote.objects.filter(pk=self.compra.pk).update(status='EXPIRADO',
                                                              data_expiracao=timezone.localdate() - timedelta(days=9))
        with self.assertRaises(RegraDeNegocio) as erro:
            pacotes_service.prorrogar_validade(self.compra, timezone.localdate() - timedelta(days=1), motivo='x')
        self.assertEqual(erro.exception.codigo, 'validade_no_passado')
        CompraPacote.objects.filter(pk=self.compra.pk).update(status='FINALIZADO')
        with self.assertRaises(RegraDeNegocio) as erro:
            pacotes_service.prorrogar_validade(self.compra, timezone.localdate() + timedelta(days=30), motivo='x')
        self.assertEqual(erro.exception.codigo, 'compra_nao_prorrogavel')

    def test_so_admin_prorroga(self):
        """Painel = ADMIN (staff_required): a profissional nao chega a view."""
        prof_user = Usuario.objects.create_user(email='prof@pacote.test', password='x',
                                                papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof)
        antes = self.compra.data_expiracao
        resp = self._post(antes + timedelta(days=30), usuario=prof_user)
        self.assertEqual(resp.status_code, 302)
        self.assertNotEqual(resp['Location'], reverse('aranha:admin_cliente_detalhe', args=[self.cli.pk]))
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.data_expiracao, antes)
        self.assertFalse(LogAuditoria.objects.filter(acao__startswith='Prorrogou').exists())


class PacoteFormTests(_Base):
    def test_criar_exige_item_e_diz_o_campo_errado(self):
        url = reverse('aranha:admin_criar_pacote')
        casos = (
            ({'nome': 'X', 'preco_total': '100', 'validade_meses': '12'}, 'Inclua ao menos um procedimento no pacote.'),
            ({'nome': 'X', 'preco_total': 'abc', 'validade_meses': '12', 'procedimento_ids': [str(self.proc.pk)],
              'quantidades': ['1']}, 'Preço inválido: use números, ex.: 1350,00.'),
            ({'nome': 'X', 'preco_total': '100', 'validade_meses': '121', 'procedimento_ids': [str(self.proc.pk)],
              'quantidades': ['1']}, 'Validade de 1 a 120 meses.'),
            ({'nome': 'X', 'preco_total': '100', 'validade_meses': '12', 'procedimento_ids': [str(self.proc.pk)],
              'quantidades': ['99999']}, 'Quantidade de sessões de 1 a 999.'),
        )
        for dados, mensagem in casos:
            with self.subTest(mensagem=mensagem):
                resp = self.client.post(url, dados)
                self.assertIn(mensagem, _mensagens(resp))
        self.assertFalse(Pacote.objects.filter(nome='X').exists())

    def test_criar_e_editar_pelo_mesmo_form(self):
        outro = criar_procedimento('Peeling')
        self.client.post(reverse('aranha:admin_criar_pacote'), {
            'nome': ' Combo ', 'preco_total': '1.200,00'.replace('.', ''), 'validade_meses': '6',
            'procedimento_ids': [str(self.proc.pk), '', str(outro.pk)], 'quantidades': ['2', '1', '3'],
        })
        pacote = Pacote.objects.get(nome='Combo')
        self.assertEqual((pacote.preco_total, pacote.validade_meses, pacote.ativo), (Decimal('1200.00'), 6, True))
        self.assertEqual(sorted(ItemPacote.objects.filter(pacote=pacote).values_list('procedimento_id',
                                                                                    'quantidade_sessoes')),
                         sorted([(self.proc.pk, 2), (outro.pk, 3)]))
        self.client.post(reverse('aranha:admin_editar_pacote', args=[pacote.pk]), {
            'nome': 'Combo', 'preco_total': '1300', 'validade_meses': '6',
            'procedimento_ids': [str(self.proc.pk)], 'quantidades': [''],
        })
        self.assertEqual(ItemPacote.objects.filter(pacote=pacote).count(), 2)   # quantidade vazia: recusado
        self.client.post(reverse('aranha:admin_editar_pacote', args=[pacote.pk]), {
            'nome': 'Combo', 'preco_total': '1300', 'validade_meses': '6', 'ativo': 'on',
            'procedimento_ids': [''], 'quantidades': ['1'],
        })
        self.assertEqual(ItemPacote.objects.filter(pacote=pacote).count(), 2)   # editar sem item: recusado

    def test_escritas_so_por_post(self):
        for nome, args in (('admin_criar_pacote', []), ('admin_editar_pacote', [self.pacote.pk]),
                           ('admin_vender_pacote', []), ('admin_prorrogar_compra_pacote', [self.compra.pk])):
            with self.subTest(nome=nome):
                self.assertEqual(self.client.get(reverse(f'aranha:{nome}', args=args)).status_code, 405)
