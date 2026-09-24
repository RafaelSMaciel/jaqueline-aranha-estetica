"""Regressoes do pacote B (views do painel/seguranca).

F4: ids de GET/POST/JSON via utils.parse.id_int ('²' passava no isdigit() e
    o int() dava 500).
F5: nota NPS (web e WhatsApp) validada com fullmatch 0..10.
F3: cancelar pacote grava CompraPacote.valor_reembolsado; receita = pago -
    reembolsado (cancelado entra com o valor retido).
F7: ProntuarioVersao no Django admin so leitura, com trilha de leitura.
F8: rotas de link magico (PREFIXOS_TOKEN) saem com no-store.
Vinculo do prontuario acompanha o max_advance_dias do profissional; ficha do
cliente registra "Indicada por" (cashback de indicacao) com auditoria.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.http import HttpResponse, QueryDict
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

import aranha_estetica.views.whatsapp as whatsapp_mod
from aranha_estetica.middleware import SecurityHeadersMiddleware
from aranha_estetica.models import (
    Atendimento,
    AvaliacaoNPS,
    Carteira,
    Cliente,
    CompraPacote,
    ConsumoSessao,
    FormularioAnamnese,
    Habilitacao,
    LogAuditoria,
    MovimentoCarteira,
    Notificacao,
    Pacote,
    Profissional,
    Prontuario,
    ProntuarioVersao,
    RespostaAnamnese,
    Usuario,
    VersaoTermo,
)
from aranha_estetica.services.fidelidade_service import FidelidadeService
from aranha_estetica.views.admin_professional import _ids_procedimentos
from aranha_estetica.views.admin_usuarios import _ler_profissional_id
from aranha_estetica.views.prontuario import _vinculo

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_compra_pacote,
    criar_pacote,
    criar_procedimento,
    criar_profissional,
)
from .test_auth_equipe import _admin, _com_totp, _token

SOBRESCRITO = '²'  # str.isdigit() -> True, int() -> ValueError


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class _AdminBase(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = Usuario.objects.create_user(
            email='admin-b@test.com', password='senha-forte-123', papel=Usuario.PAPEL_ADMIN,
        )
        self.client = Client()
        self.client.force_login(self.admin)
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)


# ════════════════════════════════════════════════════════════════════
# F4 — ids com digito Unicode nao viram 500
# ════════════════════════════════════════════════════════════════════
class IdsInvalidosTests(_AdminBase):
    def test_filtros_get_com_sobrescrito_nao_dao_500(self):
        urls = (
            reverse('aranha:painel_agendamentos') + f'?profissional={SOBRESCRITO}',
            reverse('aranha:painel_comissoes') + f'?profissional={SOBRESCRITO}',
            reverse('aranha:admin_termos_compliance') + f'?versao={SOBRESCRITO}',
            reverse('aranha:admin_calendar_events')
            + f'?profissional={SOBRESCRITO}&start=2026-01-01T00:00:00&end=2026-02-01T00:00:00',
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_filtro_de_profissional_valido_continua_filtrando(self):
        outra = criar_profissional(nome='Dra. Bia')
        cli = criar_cliente()
        meu = criar_atendimento(cli, self.prof, self.proc)
        dela = criar_atendimento(criar_cliente('Outra'), outra, self.proc)
        resp = self.client.get(reverse('aranha:painel_agendamentos') + f'?profissional={self.prof.pk}')
        ids = [a.pk for a in resp.context['agendamentos']]
        self.assertIn(meu.pk, ids)
        self.assertNotIn(dela.pk, ids)

    def test_compliance_versao_valida_e_invalida(self):
        versao = VersaoTermo.objects.create(
            tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo X', conteudo='Texto',
            versao='1.0', vigente_desde=date(2026, 1, 1), ativa=True,
        )
        url = reverse('aranha:admin_termos_compliance')
        resp = self.client.get(url + f'?versao={SOBRESCRITO}')
        self.assertIsNone(resp.context['versao_obj'])
        self.assertEqual(resp.context['versao_filter'], '')
        resp = self.client.get(url + f'?versao={versao.pk}')
        self.assertEqual(resp.context['versao_obj'], versao)

    def test_compliance_saude_mira_quem_respondeu_ficha(self):
        saude = VersaoTermo.objects.filter(tipo='SAUDE', ativa=True).first() or VersaoTermo.objects.create(
            tipo='SAUDE', titulo='Dados de saúde', conteudo='s', versao='1.0',
            vigente_desde=date(2026, 1, 1), ativa=True,
        )
        ficha = FormularioAnamnese.objects.create(nome='Ficha', tipo='ANAMNESE', escopo='GLOBAL', schema_json=[])
        pesquisa = FormularioAnamnese.objects.create(nome='Pesquisa', tipo='PESQUISA', escopo='GLOBAL', schema_json=[])
        com_ficha = criar_cliente('Com Ficha')
        RespostaAnamnese.objects.create(formulario=ficha, cliente=com_ficha, respondida_em=timezone.now())
        RespostaAnamnese.objects.create(formulario=ficha, cliente=criar_cliente('Convite Aberto'))
        RespostaAnamnese.objects.create(formulario=pesquisa, cliente=criar_cliente('So Pesquisa'),
                                        respondida_em=timezone.now())
        criar_atendimento(criar_cliente('So Agenda'), self.prof, self.proc)
        resp = self.client.get(reverse('aranha:admin_termos_compliance') + f'?versao={saude.pk}')
        linha = next(r for r in resp.context['resumo_versoes'] if r['versao'].pk == saude.pk)
        self.assertEqual((linha['relevantes'], linha['pendentes']), (1, 1))
        self.assertEqual([c.pk for c in resp.context['pendentes_lista']], [com_ficha.pk])

    def test_status_ajax_id_invalido_e_400(self):
        at = criar_atendimento(criar_cliente(), self.prof, self.proc, status='AGENDADO')
        url = reverse('aranha:admin_atualizar_status')
        for bruto in (SOBRESCRITO, '1' * 30, '-1', True, 1.5, [at.pk], None, ''):
            with self.subTest(atendimento_id=bruto):
                resp = self.client.post(
                    url, data=json.dumps({'atendimento_id': bruto, 'status': 'CONFIRMADO'}),
                    content_type='application/json',
                )
                self.assertEqual(resp.status_code, 400)
        at.refresh_from_db()
        self.assertEqual(at.status, 'AGENDADO')
        resp = self.client.post(
            url, data=json.dumps({'atendimento_id': at.pk, 'status': 'CONFIRMADO'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

    def test_anamnese_procedimento_sobrescrito_vira_erro_de_form(self):
        resp = self.client.post(reverse('aranha:admin_anamnese_criar'), {
            'nome': 'Ficha Facial', 'tipo': 'ANAMNESE', 'escopo': 'PROCEDIMENTO',
            'procedimento': SOBRESCRITO, 'schema_json': '[]', 'ativo': '1',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Escolha o procedimento do escopo.')

    def test_cadastro_profissional_ignora_procedimento_sobrescrito(self):
        self.assertEqual(
            _ids_procedimentos(QueryDict(f'procedimentos={SOBRESCRITO}&procedimentos={self.proc.pk}')),
            {self.proc.pk},
        )
        resp = self.client.post(reverse('aranha:profissional_cadastro'), {
            'nome': 'Dra. Nova', 'especialidade': '', 'ativo': 'on',
            'trabalha_segunda': 'on', 'hora_inicio_segunda': '09:00', 'hora_fim_segunda': '18:00',
            'procedimentos': [SOBRESCRITO, str(self.proc.pk)],
        })
        self.assertEqual(resp.status_code, 302)
        nova = Profissional.objects.get(nome='Dra. Nova')
        self.assertEqual(
            list(Habilitacao.objects.filter(profissional=nova).values_list('procedimento_id', flat=True)),
            [self.proc.pk],
        )
        # erro de validacao re-renderiza com os marcados (caminho que dava 500)
        resp = self.client.post(reverse('aranha:profissional_cadastro'), {
            'nome': '', 'procedimentos': [SOBRESCRITO],
        })
        self.assertEqual(resp.status_code, 200)

    def test_usuario_com_profissional_sobrescrito(self):
        self.assertEqual(_ler_profissional_id(SOBRESCRITO), (None, 'Profissional inválido.'))
        self.assertEqual(_ler_profissional_id(str(self.prof.pk)), (self.prof.pk, None))
        resp = self.client.post(reverse('aranha:admin_criar_usuario'), {
            'nome': 'Nova', 'email': 'nova@test.com', 'papel': Usuario.PAPEL_PROFISSIONAL,
            'senha': 'Senha-Forte-2026!', 'profissional_id': SOBRESCRITO,
        })
        self.assertNotEqual(resp.status_code, 500)
        self.assertFalse(Usuario.objects.filter(email='nova@test.com').exists())

    def test_termo_com_procedimento_sobrescrito(self):
        resp = self.client.post(reverse('aranha:admin_criar_termo'), {
            'tipo': 'PROCEDIMENTO', 'titulo': 'Termo', 'conteudo': 'Texto', 'versao': '1.0',
            'procedimento_id': SOBRESCRITO,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn('Procedimento inválido.', _mensagens(resp))
        self.assertFalse(VersaoTermo.objects.filter(tipo='PROCEDIMENTO').exists())

    def test_pacote_com_procedimento_sobrescrito(self):
        resp = self.client.post(reverse('aranha:admin_criar_pacote'), {
            'nome': 'Pacote X', 'preco_total': '500', 'validade_meses': '12',
            'procedimento_ids': [SOBRESCRITO], 'quantidades': ['2'],
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Pacote.objects.filter(nome='Pacote X').exists())


# ════════════════════════════════════════════════════════════════════
# F5 — nota NPS 0..10 so com digitos ASCII
# ════════════════════════════════════════════════════════════════════
class NotaNpsTests(TestCase):
    def setUp(self):
        cache.clear()
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        self.cli = criar_cliente(telefone='17988887777')
        self.atd = criar_atendimento(self.cli, prof, proc, status='REALIZADO')
        self.notif = Notificacao.objects.create(
            atendimento=self.atd, tipo='NPS', canal='WHATSAPP', status='ENVIADO', token='tok-nps-b',
        )
        self.url = reverse('aranha:nps_web', args=[self.notif.token])

    def test_nps_web_rejeita_sobrescrito_e_zero_a_esquerda(self):
        for nota in (SOBRESCRITO, '07', '11', '-1', '1²'):
            with self.subTest(nota=nota):
                resp = self.client.post(self.url, {'nota': nota})
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.context['erro'], 'Escolha uma nota de 0 a 10.')
        self.assertFalse(AvaliacaoNPS.objects.exists())

    def test_nps_web_aceita_dez(self):
        self.client.post(self.url, {'nota': '10'})
        self.assertEqual(AvaliacaoNPS.objects.get(atendimento=self.atd).nota, 10)

    def test_webhook_ignora_sobrescrito_sem_excecao(self):
        for texto in (SOBRESCRITO, '07', '1²'):
            with self.subTest(texto=texto):
                whatsapp_mod._processar_resposta_nps('5517988887777', texto)
        self.assertFalse(AvaliacaoNPS.objects.exists())
        whatsapp_mod._processar_resposta_nps('5517988887777', '0')
        self.assertEqual(AvaliacaoNPS.objects.get(atendimento=self.atd).nota, 0)

    def test_telefone_do_webhook_so_com_digitos_ascii(self):
        payload = {'entry': [{'changes': [{'value': {'messages': [
            {'from': f'5517988887777{SOBRESCRITO}', 'type': 'text', 'text': {'body': '9'}},
        ]}}]}]}
        self.assertEqual(list(whatsapp_mod._mensagens_recebidas(payload)), [('5517988887777', '9')])


# ════════════════════════════════════════════════════════════════════
# F3 — reembolso estruturado e receita liquida de pacote
# ════════════════════════════════════════════════════════════════════
class ReembolsoPacoteTests(_AdminBase):
    def setUp(self):
        super().setUp()
        self.cli = criar_cliente('Paula Pacote')
        self.pacote = criar_pacote('Drenagem 4x', preco=Decimal('1000.00'), procedimento=self.proc, sessoes=4)

    def _cancelar(self, compra, reembolso):
        return self.client.post(
            reverse('aranha:admin_cancelar_compra_pacote', args=[compra.pk]),
            {'observacao': 'Desistiu; devolvido o proporcional', 'valor_reembolsado': reembolso},
        )

    def _usar_sessoes(self, compra, n):
        for i in range(n):
            quando = timezone.now() - timedelta(days=i + 1)
            at = criar_atendimento(self.cli, self.prof, self.proc, data_hora=quando, status='AGENDADO')
            ConsumoSessao.objects.create(compra_pacote=compra, atendimento=at)
            Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal('250.00'))

    def test_cancelamento_grava_valor_reembolsado(self):
        compra = criar_compra_pacote(self.cli, self.pacote)
        self._cancelar(compra, '600,00')
        compra.refresh_from_db()
        self.assertEqual(compra.status, 'CANCELADO')
        self.assertEqual(compra.valor_reembolsado, Decimal('600.00'))
        resp = self.client.get(reverse('aranha:admin_cliente_detalhe', args=[self.cli.pk]))
        self.assertContains(resp, 'Devolvido R$ 600,00')

    def test_cancelamento_recusado_nao_grava_reembolso(self):
        compra = criar_compra_pacote(self.cli, self.pacote)
        self._cancelar(compra, '1000,01')
        compra.refresh_from_db()
        self.assertEqual(compra.status, 'ATIVO')
        self.assertEqual(compra.valor_reembolsado, Decimal('0'))

    def test_financeiro_pacote_parcialmente_usado_entra_liquido(self):
        compra = criar_compra_pacote(self.cli, self.pacote)
        self._usar_sessoes(compra, 2)
        self._cancelar(compra, '500,00')
        # devolvido integral: liquido 0, nao conta como venda
        integral = criar_compra_pacote(criar_cliente('Rita'), self.pacote)
        self._cancelar(integral, '1000,00')
        cache.clear()
        ctx = self.client.get(reverse('aranha:dashboard_financeiro')).context
        # sessoes de pacote nao somam; a venda cancelada entra com os 500 retidos
        self.assertEqual(ctx['fat_hoje_total'], Decimal('500.00'))
        self.assertEqual(ctx['fat_hoje_pacotes'], 1)
        self.assertEqual(ctx['fat_mes_pacotes_total'], Decimal('500.00'))

    def test_financeiro_pacote_ativo_segue_com_valor_pago(self):
        criar_compra_pacote(self.cli, self.pacote)
        ctx = self.client.get(reverse('aranha:dashboard_financeiro')).context
        self.assertEqual(ctx['fat_hoje_total'], Decimal('1000.00'))

    def test_overview_receita_mensal_liquida_do_reembolso(self):
        compra = criar_compra_pacote(self.cli, self.pacote)
        self._usar_sessoes(compra, 1)
        self._cancelar(compra, '750,00')
        criar_compra_pacote(criar_cliente('Ativa'), criar_pacote('Outro', preco=Decimal('300.00')))
        resp = self.client.get(reverse('aranha:painel_overview'))
        # 250 retidos do cancelado + 300 do ativo
        self.assertEqual(resp.context['receita_mensal'], '550,00')


# ════════════════════════════════════════════════════════════════════
# F7 — historico do prontuario no Django admin: so leitura + trilha
# ════════════════════════════════════════════════════════════════════
class ProntuarioVersaoAdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = _admin(email='adm-b@test.com')
        device = _com_totp(self.admin)
        self.c = Client()
        self.c.force_login(self.admin)
        self.c.post(reverse('aranha:admin_2fa_verify'), {'token': _token(device)})
        self.cliente = criar_cliente(telefone='17999990002')
        self.pront = Prontuario.objects.create(cliente=self.cliente, alergias='Dipirona')
        self.versao = ProntuarioVersao.objects.create(prontuario=self.pront, dados={'alergias': 'Látex'})
        self.base = '/django-admin-sv/aranha_estetica/prontuarioversao/'

    def test_sem_incluir_alterar_ou_excluir(self):
        self.assertEqual(self.c.get(self.base + 'add/').status_code, 403)
        resp = self.c.post(f'{self.base}{self.versao.pk}/change/', {
            'prontuario': self.pront.pk, 'dados': '{"alergias": "nenhuma"}',
        })
        self.assertEqual(resp.status_code, 403)
        resp = self.c.post(f'{self.base}{self.versao.pk}/delete/', {'post': 'yes'})
        self.assertEqual(resp.status_code, 403)
        self.versao.refresh_from_db()
        self.assertEqual(self.versao.dados, {'alergias': 'Látex'})

    def test_leitura_entra_na_trilha_do_prontuario(self):
        self.assertEqual(self.c.get(self.base).status_code, 200)
        resp = self.c.get(f'{self.base}{self.versao.pk}/change/')
        self.assertEqual(resp.status_code, 200)
        log = LogAuditoria.objects.get(acao='Visualizou versao do prontuario (django-admin)')
        self.assertEqual(log.tabela, 'prontuario')
        self.assertEqual(log.registro_id, self.cliente.pk)
        self.assertEqual(log.detalhes['prontuario_versao_id'], self.versao.pk)


# ════════════════════════════════════════════════════════════════════
# F8 — link magico sem cache no navegador
# ════════════════════════════════════════════════════════════════════
class NoStoreRotasTokenTests(TestCase):
    def test_pagina_de_token_sai_com_no_store(self):
        prof = criar_profissional()
        atd = criar_atendimento(criar_cliente(), prof, criar_procedimento(profissional=prof), status='REALIZADO')
        notif = Notificacao.objects.create(atendimento=atd, tipo='NPS', canal='EMAIL', token='tok-cache-b')
        resp = Client().get(reverse('aranha:nps_web', args=[notif.token]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('no-store', resp['Cache-Control'])
        self.assertEqual(resp['Referrer-Policy'], 'no-referrer')

    def test_todos_os_prefixos_de_token_sem_cache(self):
        mw = SecurityHeadersMiddleware(lambda _r: HttpResponse('ok'))
        for prefixo in SecurityHeadersMiddleware.PREFIXOS_TOKEN:
            with self.subTest(prefixo=prefixo):
                resp = mw(RequestFactory().get(f'{prefixo}abc123/'))
                self.assertIn('no-store', resp['Cache-Control'])

    def test_cache_control_da_view_e_respeitado_em_rota_de_token(self):
        def view(_request):
            resp = HttpResponse('ok')
            resp['Cache-Control'] = 'private, max-age=300'
            return resp
        resp = SecurityHeadersMiddleware(view)(RequestFactory().get('/agenda/dra-ana/feed.ics'))
        self.assertEqual(resp['Cache-Control'], 'private, max-age=300')

    def test_site_publico_segue_sem_no_store(self):
        mw = SecurityHeadersMiddleware(lambda _r: HttpResponse('ok'))
        self.assertFalse(mw(RequestFactory().get('/agendamento/')).has_header('Cache-Control'))


# ════════════════════════════════════════════════════════════════════
# Vinculo do prontuario: janela futura = max(60, max_advance_dias)
# ════════════════════════════════════════════════════════════════════
class VinculoJanelaFuturaTests(TestCase):
    def setUp(self):
        self.cli = criar_cliente()

    def _marcado_em(self, prof, dias):
        proc = criar_procedimento(nome=f'Proc {prof.pk}-{dias}', profissional=prof)
        quando = (timezone.now() + timedelta(days=dias)).replace(hour=10, minute=0, second=0, microsecond=0)
        return criar_atendimento(self.cli, prof, proc, data_hora=quando, status='AGENDADO')

    def test_antecedencia_maior_que_60_amplia_a_janela(self):
        prof = criar_profissional(nome='Dra. Longe', max_advance_dias=120)
        at = self._marcado_em(prof, 100)
        self.assertEqual(_vinculo(prof, self.cli), at)
        self.assertEqual(_vinculo(prof, self.cli, escrita=True), at)

    def test_antecedencia_menor_mantem_60_dias(self):
        prof = criar_profissional(nome='Dra. Perto', max_advance_dias=30)
        at = self._marcado_em(prof, 50)
        self.assertEqual(_vinculo(prof, self.cli), at)
        Atendimento.objects.filter(pk=at.pk).update(
            data_hora_inicio=at.data_hora_inicio + timedelta(days=40),
            data_hora_fim=at.data_hora_fim + timedelta(days=40),
        )
        self.assertIsNone(_vinculo(prof, self.cli))


# ════════════════════════════════════════════════════════════════════
# Ficha do cliente: "Indicada por" (cashback de indicacao)
# ════════════════════════════════════════════════════════════════════
class IndicacaoFichaClienteTests(_AdminBase):
    def setUp(self):
        super().setUp()
        self.cli = criar_cliente('Bruna Nova')
        self.indicadora = criar_cliente('Carla Indicadora')
        self.url = reverse('aranha:admin_cliente_detalhe', args=[self.cli.pk])

    def _indicar(self, **dados):
        return self.client.post(self.url, {'acao': 'indicacao', **dados})

    def test_por_codigo_grava_e_audita_sem_nome(self):
        resp = self._indicar(indicado_por_busca=f' {self.indicadora.codigo_indicacao.lower()} ')
        self.assertEqual(resp.status_code, 302)
        self.cli.refresh_from_db()
        self.assertEqual(self.cli.indicado_por, self.indicadora)
        self.assertEqual(self.cli.nome, 'Bruna Nova')  # cadastro nao passa pelo form
        log = LogAuditoria.objects.get(acao='Registrou indicacao')
        self.assertEqual((log.tabela, log.registro_id), ('cliente', self.cli.pk))
        self.assertEqual(log.detalhes['indicado_por'], self.indicadora.pk)
        self.assertIsNone(log.detalhes['indicado_por_anterior'])
        self.assertNotIn('Carla', log.acao)
        pagina = self.client.get(self.url)
        self.assertContains(pagina, self.cli.codigo_indicacao)
        self.assertContains(pagina, 'Carla Indicadora')

    def test_por_nome(self):
        self._indicar(indicado_por_busca='carla indicadora')
        self.cli.refresh_from_db()
        self.assertEqual(self.cli.indicado_por_id, self.indicadora.pk)

    def test_nao_indica_a_si_mesma(self):
        resp = self._indicar(indicado_por_busca=self.cli.codigo_indicacao)
        self.assertIn('Uma cliente não pode indicar a si mesma.', _mensagens(resp))
        self.cli.refresh_from_db()
        self.assertIsNone(self.cli.indicado_por_id)
        self.assertFalse(LogAuditoria.objects.filter(acao='Registrou indicacao').exists())

    def test_nome_ambiguo_ou_inexistente(self):
        criar_cliente('Carla Souza')
        resp = self._indicar(indicado_por_busca='Carla')
        self.assertTrue(any('Mais de uma cliente' in m for m in _mensagens(resp)))
        resp = self._indicar(indicado_por_busca='Ninguem Assim')
        self.assertTrue(any('Nenhuma cliente encontrada' in m for m in _mensagens(resp)))
        resp = self._indicar(indicado_por_busca='')
        self.assertTrue(any('Informe o código' in m for m in _mensagens(resp)))
        self.cli.refresh_from_db()
        self.assertIsNone(self.cli.indicado_por_id)

    def test_remover(self):
        Cliente.objects.filter(pk=self.cli.pk).update(indicado_por=self.indicadora)
        self._indicar(remover='1')
        self.cli.refresh_from_db()
        self.assertIsNone(self.cli.indicado_por_id)
        log = LogAuditoria.objects.get(acao='Removeu indicacao')
        self.assertEqual(log.detalhes['indicado_por_anterior'], self.indicadora.pk)

    def test_aviso_quando_ja_tem_atendimento_pago(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, status='AGENDADO')
        Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal('200.00'))
        resp = self._indicar(indicado_por_busca=self.indicadora.codigo_indicacao)
        self.assertTrue(any('não haverá crédito' in m for m in _mensagens(resp)))

    def test_indicacao_gera_cashback_e_depois_trava(self):
        self._indicar(indicado_por_busca=self.indicadora.codigo_indicacao)
        at = criar_atendimento(self.cli, self.prof, self.proc, status='AGENDADO')
        Atendimento.objects.filter(pk=at.pk).update(status='REALIZADO', valor_cobrado=Decimal('200.00'))
        at.refresh_from_db()
        FidelidadeService.liberar_cashback_indicacao(at)
        self.assertTrue(MovimentoCarteira.objects.filter(
            carteira=Carteira.objects.get(cliente=self.indicadora), origem='CASHBACK_INDICACAO',
        ).exists())
        outra = criar_cliente('Dora Outra')
        resp = self._indicar(indicado_por_busca=outra.codigo_indicacao)
        self.assertTrue(any('já gerou cashback' in m for m in _mensagens(resp)))
        self.cli.refresh_from_db()
        self.assertEqual(self.cli.indicado_por_id, self.indicadora.pk)
        self.assertContains(self.client.get(self.url), 'não pode mais ser alterada')

    def test_post_do_cadastro_segue_funcionando(self):
        resp = self.client.post(self.url, {'nome': 'Bruna Editada', 'telefone': self.cli.telefone, 'ativo': '1'})
        self.assertEqual(resp.status_code, 302)
        self.cli.refresh_from_db()
        self.assertEqual(self.cli.nome, 'Bruna Editada')


class CompraPacoteReembolsoCheckTests(TestCase):
    """Sanidade do contrato 2 usado pelas views (campo existe e default 0)."""

    def test_default_zero(self):
        compra = criar_compra_pacote(criar_cliente(), criar_pacote())
        self.assertEqual(CompraPacote.objects.get(pk=compra.pk).valor_reembolsado, Decimal('0'))
