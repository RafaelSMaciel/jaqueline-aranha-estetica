"""Promocoes no painel: criar/editar pelo mesmo form, excluir e o disparo por e-mail em lotes."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.messages import get_messages
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import Configuracao, Promocao, Usuario
from aranha_estetica.services import consentimentos, marketing

from ..factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class _AdminTestCase(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email='admin-promo@test.com', password='senha-forte-123', nome='Admin Promo',
        )
        self.client.force_login(self.admin)
        self.proc = criar_procedimento()
        self.hoje = timezone.localdate()


# ─── Criar e editar (PromocaoForm) ────────────────────────────────────
class PromocaoCriacaoExclusaoTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.datas = {'data_inicio': self.hoje.isoformat(), 'data_fim': (self.hoje + timedelta(days=10)).isoformat(),
                      'ativa': '1', 'descricao': ''}

    def _criar(self, **campos):
        return self.client.post(reverse('aranha:admin_criar_promocao'), {**self.datas, **campos}, follow=True)

    def test_geral_percentual_e_especifica_preco_fixo(self):
        self._criar(nome='Semana da pele', desconto='15')
        geral = Promocao.objects.get(nome='Semana da pele')
        self.assertIsNone(geral.procedimento)
        self.assertEqual(geral.desconto_percentual, Decimal('15.00'))
        self._criar(nome='Limpeza por 99', procedimento=str(self.proc.pk), preco_promocional='99,00')
        fixa = Promocao.objects.get(nome='Limpeza por 99')
        self.assertEqual((fixa.preco_promocional, fixa.desconto_percentual), (Decimal('99.00'), Decimal('0')))
        resp = self._criar(nome='Fixa sem procedimento', preco_promocional='50')
        self.assertFalse(Promocao.objects.filter(nome='Fixa sem procedimento').exists())
        self.assertTrue(any('escolha o procedimento' in m for m in _mensagens(resp)))

    def test_procedimento_nao_numerico_e_erro_de_form_nao_500(self):
        resp = self._criar(nome='Quebrada', procedimento='abc', desconto='10')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any('Procedimento inválido.' in m for m in _mensagens(resp)))
        self.assertFalse(Promocao.objects.filter(nome='Quebrada').exists())

    def test_desconto_e_preco_juntos_ou_nenhum_recusam(self):
        for campos in ({'desconto': '10', 'preco_promocional': '50'}, {}, {'desconto': '0'}, {'desconto': '101'}):
            with self.subTest(campos=campos):
                resp = self._criar(nome='Ambigua', procedimento=str(self.proc.pk), **campos)
                self.assertTrue(any('Informe o desconto (até 100%) ou o preço fixo.' in m for m in _mensagens(resp)))
        self.assertFalse(Promocao.objects.filter(nome='Ambigua').exists())

    def test_fim_antes_do_inicio_recusa(self):
        resp = self._criar(nome='Invertida', desconto='10', data_fim=(self.hoje - timedelta(days=1)).isoformat())
        self.assertTrue(any('A data de fim deve ser igual ou posterior à de início.' in m for m in _mensagens(resp)))
        self.assertFalse(Promocao.objects.filter(nome='Invertida').exists())

    def test_get_nas_escritas_nao_altera(self):
        for rota in ('aranha:admin_criar_promocao',):
            self.assertEqual(self.client.get(reverse(rota)).status_code, 405)

    def test_promocao_usada_nao_e_excluida_e_inexistente_da_404(self):
        promo = Promocao.objects.create(nome='Usada', desconto_percentual=Decimal('10'), procedimento=self.proc,
                                        data_inicio=self.hoje, data_fim=self.hoje)
        at = criar_atendimento(criar_cliente(), criar_profissional(), self.proc)
        at.promocao = promo
        at.save(update_fields=['promocao'])
        resp = self.client.post(reverse('aranha:admin_excluir_promocao', args=[promo.pk]), follow=True)
        self.assertTrue(Promocao.objects.filter(pk=promo.pk).exists())
        self.assertTrue(any('desative em vez de excluir' in m for m in _mensagens(resp)))
        self.assertEqual(self.client.post(reverse('aranha:admin_excluir_promocao', args=[999999])).status_code, 404)

    def test_exclui_a_nao_usada(self):
        promo = Promocao.objects.create(nome='Livre', desconto_percentual=Decimal('10'),
                                        data_inicio=self.hoje, data_fim=self.hoje)
        self.client.post(reverse('aranha:admin_excluir_promocao', args=[promo.pk]))
        self.assertFalse(Promocao.objects.filter(pk=promo.pk).exists())


class PromocaoEdicaoTests(_AdminTestCase):
    def _post(self, promo, **extra):
        data = {'nome': promo.nome, 'descricao': '', 'ativa': '0',
                'data_inicio': promo.data_inicio.isoformat(), 'data_fim': promo.data_fim.isoformat()}
        data.update(extra)
        return self.client.post(reverse('aranha:admin_editar_promocao', args=[promo.pk]), data)

    def test_editar_desconto_decimal_com_virgula(self):
        promo = Promocao.objects.create(nome='Primavera', desconto_percentual=Decimal('15.00'), procedimento=self.proc,
                                        data_inicio=self.hoje, data_fim=self.hoje + timedelta(days=30))
        self._post(promo, procedimento=self.proc.pk, desconto='12,5', ativa='1')
        promo.refresh_from_db()
        self.assertEqual((promo.desconto_percentual, promo.ativa), (Decimal('12.50'), True))

    def test_global_continua_global(self):
        promo = Promocao.objects.create(nome='Global', desconto_percentual=Decimal('10'),
                                        data_inicio=self.hoje, data_fim=self.hoje + timedelta(days=10))
        resp = self.client.get(reverse('aranha:admin_promocoes'))
        self.assertContains(resp, '<option value="" selected>Todos os procedimentos</option>', html=True)
        self._post(promo, procedimento='', desconto='10')
        promo.refresh_from_db()
        self.assertIsNone(promo.procedimento_id)
        self.assertFalse(promo.ativa)

    def test_preco_fixo_pode_ser_desativado(self):
        promo = Promocao.objects.create(nome='Fixa', preco_promocional=Decimal('99.00'), procedimento=self.proc,
                                        data_inicio=self.hoje, data_fim=self.hoje + timedelta(days=10))
        resp = self.client.get(reverse('aranha:admin_promocoes'))
        self.assertContains(resp, 'name="preco_promocional"')
        self.assertContains(resp, 'R$ 99,00')
        self.assertNotContains(resp, 'name="cupom"')
        resp = self._post(promo, procedimento=str(self.proc.pk), preco_promocional='99.00')
        promo.refresh_from_db()
        self.assertFalse(promo.ativa)
        self.assertEqual((promo.preco_promocional, promo.desconto_percentual), (Decimal('99.00'), Decimal('0')))
        self.assertEqual(resp.status_code, 302)   # aceito: volta p/ a lista (recusado devolveria 200)


# ─── Disparo por e-mail (services/marketing) ──────────────────────────
@override_settings(EMAIL_BACKEND=LOCMEM, SITE_URL='https://clinica.example.com')
class PromocaoDisparoTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.promo = Promocao.objects.create(
            nome='Setembro Glow', preco_promocional=Decimal('99.00'), procedimento=self.proc,
            data_inicio=self.hoje, data_fim=self.hoje + timedelta(days=10),
        )
        self.url = reverse('aranha:admin_disparar_promocao', args=[self.promo.pk])

    def _clientes(self, n):
        return [criar_cliente(f'Cli {i}', email=f'cli{i}@test.com', consent_email_marketing=True) for i in range(n)]

    def test_lista_mostra_o_botao_de_disparo_sem_validade(self):
        resp = self.client.get(reverse('aranha:admin_promocoes'))
        self.assertContains(resp, self.url)
        self.assertNotContains(resp, 'validade_dias')

    def test_email_diz_o_periodo_e_o_preco_fixo(self):
        self._clientes(1)
        self.client.post(self.url)
        msg = mail.outbox[0]
        self.assertIn('99,00', msg.subject)
        self.assertNotIn('0% OFF', msg.subject)
        corpo = msg.alternatives[0][0]
        self.assertIn(f'Para atendimentos de {self.hoje:%d/%m/%Y} a {self.promo.data_fim:%d/%m/%Y}', corpo)
        self.assertIn(f'/agendamento/?procedimento={self.proc.pk}', corpo)

    def test_lotes_com_cursor_na_promocao_sem_reenvio(self):
        self._clientes(marketing.LOTE_PROMOCAO + 5)
        # revogada (descadastro) e quem nunca autorizou ficam fora do publico
        revogada = criar_cliente('Revogada', email='revogada@test.com', consent_email_marketing=True)
        consentimentos.revogar(revogada, 'MARKETING', 'EMAIL', origem='DESCADASTRO')
        criar_cliente('Sem opt-in', email='semoptin@test.com')

        resp = self.client.post(self.url, follow=True)
        self.assertEqual(len(mail.outbox), marketing.LOTE_PROMOCAO)
        self.promo.refresh_from_db()
        self.assertIsNotNone(self.promo.envio_cursor)
        self.assertIsNotNone(self.promo.disparada_em)
        self.assertTrue(any('Faltam 5 cliente(s)' in m for m in _mensagens(resp)))
        self.assertContains(resp, 'Continuar envio')

        self.client.post(self.url)
        self.assertEqual(len(mail.outbox), marketing.LOTE_PROMOCAO + 5)
        destinatarios = [m.to[0] for m in mail.outbox]
        self.assertEqual(len(destinatarios), len(set(destinatarios)))
        self.assertNotIn('revogada@test.com', destinatarios)
        self.promo.refresh_from_db()
        self.assertIsNone(self.promo.envio_cursor)
        self.assertFalse(Configuracao.objects.filter(chave__startswith='promocao_').exists())

    def test_segundo_disparo_sem_confirmacao_e_recusado(self):
        self._clientes(2)
        self.client.post(self.url)
        self.assertEqual(len(mail.outbox), 2)
        resp = self.client.post(self.url, follow=True)
        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(any('já foi enviada a todos' in m for m in _mensagens(resp)))
        self.assertContains(resp, 'Reenviar para todos')
        self.client.post(self.url, {'reenviar_para_todos': '1'})
        self.assertEqual(len(mail.outbox), 4)

    def test_lote_e_reservado_antes_do_envio_falha_nao_volta_o_cursor(self):
        """Exatamente uma vez: e-mail que falhou nao e reenviado no clique seguinte."""
        self._clientes(3)
        from unittest import mock
        with mock.patch('aranha_estetica.utils.email.enviar_promocao_email', side_effect=[True, RuntimeError, True]):
            enviados, falhas, restantes = marketing.enviar_promocao(self.promo)
        self.assertEqual((enviados, falhas, restantes), (2, 1, 0))
        with self.assertRaises(marketing.PromocaoJaDisparada):
            marketing.enviar_promocao(self.promo)

    def test_promocao_expirada_nao_dispara(self):
        self._clientes(1)
        Promocao.objects.filter(pk=self.promo.pk).update(
            data_inicio=self.hoje - timedelta(days=20), data_fim=self.hoje - timedelta(days=1))
        resp = self.client.post(self.url, follow=True)
        self.assertEqual(mail.outbox, [])
        self.assertTrue(any('não está vigente' in m for m in _mensagens(resp)))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend', DEBUG=False)
    def test_email_nao_configurado_nao_dispara_nem_marca(self):
        self._clientes(1)
        resp = self.client.post(self.url, follow=True)
        self.assertTrue(any('não está configurado' in m for m in _mensagens(resp)))
        self.promo.refresh_from_db()
        self.assertIsNone(self.promo.disparada_em)

    def test_sem_destinatarios_avisa(self):
        resp = self.client.post(self.url, follow=True)
        self.assertTrue(any('Nenhum cliente autorizou' in m for m in _mensagens(resp)))
