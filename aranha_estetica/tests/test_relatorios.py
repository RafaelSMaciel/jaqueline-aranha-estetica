"""Telas de relatorio do admin: NPS e Comissoes."""
from decimal import Decimal

from django.test import Client, TestCase

from aranha_estetica.models import AvaliacaoNPS, MovimentoComissao, Usuario

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_procedimento,
    criar_profissional,
)


class RelatoriosAdminTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_user(
            email='admin@rel.com', password='senha123', papel=Usuario.PAPEL_ADMIN,
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def _comissao(self, status=MovimentoComissao.STATUS_PENDENTE, valor='50.00'):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cli = criar_cliente()
        at = criar_atendimento(cli, prof, proc, status='REALIZADO')
        return MovimentoComissao.objects.create(
            profissional=prof, atendimento=at, valor=Decimal(valor), status=status,
        )

    def test_nps_carrega_vazio(self):
        resp = self.client.get('/painel/nps/')
        self.assertEqual(resp.status_code, 200)

    def test_nps_carrega_com_dados(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cli = criar_cliente()
        at = criar_atendimento(cli, prof, proc, status='REALIZADO')
        AvaliacaoNPS.objects.create(atendimento=at, nota=10, comentario='Excelente!')
        resp = self.client.get('/painel/nps/?periodo=tudo')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Excelente!')

    def test_comissoes_carrega(self):
        self._comissao()
        resp = self.client.get('/painel/comissoes/')
        self.assertEqual(resp.status_code, 200)

    def test_marcar_comissao_paga(self):
        mov = self._comissao()
        resp = self.client.post(f'/painel/comissoes/{mov.pk}/pagar/')
        self.assertEqual(resp.status_code, 302)
        mov.refresh_from_db()
        self.assertEqual(mov.status, MovimentoComissao.STATUS_PAGA)
        self.assertIsNotNone(mov.pago_em)

    def test_pagar_idempotente_quando_nao_pendente(self):
        mov = self._comissao(status=MovimentoComissao.STATUS_PAGA)
        resp = self.client.post(f'/painel/comissoes/{mov.pk}/pagar/')
        self.assertEqual(resp.status_code, 302)
        mov.refresh_from_db()
        self.assertEqual(mov.status, MovimentoComissao.STATUS_PAGA)

    def test_pagar_exige_post(self):
        mov = self._comissao()
        resp = self.client.get(f'/painel/comissoes/{mov.pk}/pagar/')
        self.assertEqual(resp.status_code, 405)

    def test_telas_bloqueiam_anonimo(self):
        c = Client()
        self.assertIn(c.get('/painel/nps/').status_code, [302, 403])
        self.assertIn(c.get('/painel/comissoes/').status_code, [302, 403])

    # ── Moderacao de depoimentos (contrato 4) ──
    def _avaliacao(self, autoriza=True, comentario='Amei o atendimento!'):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        at = criar_atendimento(criar_cliente(), prof, proc, status='REALIZADO')
        return AvaliacaoNPS.objects.create(
            atendimento=at, nota=10, comentario=comentario, autoriza_publicacao=autoriza,
        )

    def test_aprovar_depoimento_com_opt_in(self):
        av = self._avaliacao()
        resp = self.client.get('/painel/nps/')
        self.assertIn(av, resp.context['aguardando_moderacao'])
        resp = self.client.post(f'/painel/nps/{av.pk}/publicacao/', {'acao': 'aprovar'})
        self.assertEqual(resp.status_code, 302)
        av.refresh_from_db()
        self.assertTrue(av.aprovado_publicacao)
        resp = self.client.post(f'/painel/nps/{av.pk}/publicacao/', {'acao': 'reprovar'})
        av.refresh_from_db()
        self.assertFalse(av.aprovado_publicacao)

    def test_nao_publica_sem_opt_in_do_cliente(self):
        av = self._avaliacao(autoriza=False)
        self.client.post(f'/painel/nps/{av.pk}/publicacao/', {'acao': 'aprovar'})
        av.refresh_from_db()
        self.assertFalse(av.aprovado_publicacao)

    def test_publicacao_exige_post(self):
        av = self._avaliacao()
        self.assertEqual(self.client.get(f'/painel/nps/{av.pk}/publicacao/').status_code, 405)

    def test_pluralizacao_avaliacoes(self):
        self._avaliacao()
        self._avaliacao()
        resp = self.client.get('/painel/nps/?periodo=tudo')
        self.assertContains(resp, '2 avaliações')
        self.assertNotContains(resp, 'avaliaçãoões')

    def test_resumo_por_profissional_pendente_primeiro(self):
        # Profissional so com comissao PAGA (pendente NULL) nao pode ir ao topo (NULLS FIRST no PG)
        self._comissao(status=MovimentoComissao.STATUS_PAGA, valor='90.00')
        pend = self._comissao(status=MovimentoComissao.STATUS_PENDENTE, valor='10.00')
        resp = self.client.get('/painel/comissoes/')
        resumo = resp.context['por_profissional']
        self.assertEqual(resumo[0]['pendente'], pend.valor)

    def test_paginacao_preserva_filtros_codificados(self):
        for _ in range(51):
            self._comissao()
        resp = self.client.get('/painel/comissoes/', {'status': 'PENDENTE', 'periodo': 'tudo'})
        self.assertContains(resp, 'status=PENDENTE')
        self.assertContains(resp, 'page=2')
