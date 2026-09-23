"""Regressao da rodada 3 do painel operacional (P5): portal do profissional
(vinculo, trilha, autoria legada, no-store, CSRF do push), aprovacao com IP,
valores com teto, pacote cancelado com reembolso, promocoes global/preco fixo,
cliente inativa no agendamento interno, cashback ao registrar valor, ficha de
anamnese pendente, lista de espera e previa de e-mails."""
import json
import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock, skipUnless

from django.core import mail
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import (
    AnotacaoSessao,
    Atendimento,
    CompraPacote,
    FormularioAnamnese,
    ListaEspera,
    LogAuditoria,
    MovimentoCarteira,
    Pacote,
    Procedimento,
    Promocao,
    Prontuario,
    RespostaAnamnese,
    Usuario,
)

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_compra_pacote,
    criar_pacote,
    criar_procedimento,
    criar_profissional,
)
from .test_painel_operacional import _AdminTestCase, _local, _mensagens

TEMPLATES = Path(__file__).resolve().parent.parent / 'templates'


def _prof_usuario(prof, email):
    return Usuario.objects.create_user(
        email=email, password='senha-forte-123', nome=prof.nome,
        papel=Usuario.PAPEL_PROFISSIONAL, profissional=prof,
    )


# ─── Portal: anotar (autoria legada, trilha, vinculo) e no-store ───────
class PortalAnotarTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional('Dra. Portal R3')
        self.proc = criar_procedimento(profissional=self.prof)
        self.user = _prof_usuario(self.prof, 'prof-r3@test.com')
        self.cli = criar_cliente('Dora Alergica')
        Prontuario.objects.create(cliente=self.cli, alergias='Lidocaina')
        self.agendado = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(1, 10))
        self.client.force_login(self.user)

    def _url(self, at):
        return reverse('aranha:profissional_anotar', args=[at.pk])

    def test_nota_sem_autor_legada_nao_da_500(self):
        """followups-anotar-autor-500: autor NULL (SET_NULL legado) derrubava a tela."""
        AnotacaoSessao.objects.create(atendimento=self.agendado, autor=None,
                                      autor_nome='Dra. Antiga', texto='nota com snapshot')
        AnotacaoSessao.objects.create(atendimento=self.agendado, autor=None,
                                      autor_nome='', texto='nota sem autor nenhum')
        resp = self.client.get(self._url(self.agendado))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Dra. Antiga')
        self.assertContains(resp, 'Autor não identificado')

    def test_leitura_e_escrita_entram_na_trilha_com_ip(self):
        resp = self.client.get(self._url(self.agendado))
        self.assertContains(resp, 'Lidocaina')
        leitura = LogAuditoria.objects.get(acao='Acessou prontuario', tabela='prontuario',
                                           registro_id=self.cli.pk)
        self.assertEqual(leitura.ip_origem, '127.0.0.1')
        self.assertEqual(leitura.detalhes['view'], 'profissional.anotar')

        resp = self.client.post(self._url(self.agendado), {'texto': 'Sem intercorrências'})
        self.assertEqual(resp.status_code, 302)
        nota = AnotacaoSessao.objects.get(atendimento=self.agendado)
        escrita = LogAuditoria.objects.get(acao='Criou anotacao', tabela='anotacao_sessao',
                                           registro_id=nota.pk)
        self.assertEqual(escrita.ip_origem, '127.0.0.1')
        self.assertEqual(escrita.detalhes['cliente'], self.cli.pk)

    def test_paginas_com_dado_de_saude_sem_cache(self):
        for url in (reverse('aranha:profissional_agenda'), self._url(self.agendado)):
            resp = self.client.get(url)
            self.assertIn('no-store', resp['Cache-Control'], url)

    def test_sem_vinculo_nao_ve_alerta_ficha_nem_historico(self):
        """rev_painel-02/09: CANCELADO de 400 dias nao abre dado de saude no portal."""
        vera = criar_cliente('Vera Antiga')
        Prontuario.objects.create(cliente=vera, alergias='Latex XYZ')
        antigo = criar_atendimento(vera, self.prof, self.proc, data_hora=_local(-400, 10),
                                   status='CANCELADO')
        AnotacaoSessao.objects.create(atendimento=antigo, autor=None, autor_nome='X',
                                      texto='nota antiga sigilosa')
        ficha_vera = reverse('aranha:prontuario_detalhe', args=[vera.pk])

        resp = self.client.get(reverse('aranha:profissional_agenda'),
                               {'data': _local(-400).date().isoformat()})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Vera Antiga')
        self.assertNotContains(resp, 'Latex XYZ')
        self.assertNotContains(resp, ficha_vera)

        resp = self.client.get(self._url(antigo))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Latex XYZ')
        self.assertNotContains(resp, 'nota antiga sigilosa')
        self.assertNotContains(resp, ficha_vera)
        self.assertFalse(LogAuditoria.objects.filter(acao='Acessou prontuario', registro_id=vera.pk).exists())

        # escrever a nota do proprio atendimento continua permitido (e auditado)
        resp = self.client.post(self._url(antigo), {'texto': 'retorno de ligação'})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(LogAuditoria.objects.filter(acao='Criou anotacao', detalhes__cliente=vera.pk).exists())

    def test_com_vinculo_mostra_ficha_e_alerta(self):
        resp = self.client.get(reverse('aranha:profissional_agenda'),
                               {'data': _local(1).date().isoformat()})
        self.assertContains(resp, 'Lidocaina')
        self.assertContains(resp, reverse('aranha:prontuario_detalhe', args=[self.cli.pk]))

    def test_meta_csrf_para_inscricao_de_push(self):
        """crawl-1: cookie csrftoken e HttpOnly; o webpush.js le o token da meta."""
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.user)
        resp = cliente.get(reverse('aranha:profissional_agenda'))
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', resp.content.decode())
        self.assertIsNotNone(m)
        payload = {'endpoint': 'https://push.example.com/abc',
                   'keys': {'p256dh': 'chave-p256dh', 'auth': 'chave-auth'}}
        resp = cliente.post(reverse('aranha:webpush_subscribe'), json.dumps(payload),
                            content_type='application/json', HTTP_X_CSRFTOKEN=m.group(1))
        self.assertEqual(resp.status_code, 200)


class PortalFichaNoStoreClienteTests(_AdminTestCase):
    def test_ficha_do_cliente_sem_cache(self):
        cli = criar_cliente('Ficha Privada')
        resp = self.client.get(reverse('aranha:admin_cliente_detalhe', args=[cli.pk]))
        self.assertIn('no-store', resp['Cache-Control'])


# ─── Aprovar/rejeitar: auditoria com IP ───────────────────────────────
class AprovacaoComIpTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional('Dra. IP')
        self.proc = criar_procedimento(profissional=self.prof)
        self.a1 = criar_atendimento(criar_cliente('A1'), self.prof, self.proc, data_hora=_local(3, 10),
                                    status='PENDENTE')
        self.a2 = criar_atendimento(criar_cliente('A2'), self.prof, self.proc, data_hora=_local(3, 14),
                                    status='PENDENTE')

    def _ip(self, acao, at):
        return LogAuditoria.objects.get(acao=acao, registro_id=at.pk).ip_origem

    def test_portal_aprovar_e_rejeitar(self):
        self.client.force_login(_prof_usuario(self.prof, 'prof-ip@test.com'))
        self.client.post(reverse('aranha:profissional_aprovar', args=[self.a1.pk]))
        self.client.post(reverse('aranha:profissional_rejeitar', args=[self.a2.pk]))
        self.assertEqual(self._ip('Aprovou agendamento', self.a1), '127.0.0.1')
        self.assertEqual(self._ip('Rejeitou agendamento', self.a2), '127.0.0.1')

    def test_painel_individual_e_lote(self):
        admin = Usuario.objects.create_superuser(email='adm-ip@test.com', password='x-senha-123', nome='Adm')
        self.client.force_login(admin)
        self.client.post(reverse('aranha:admin_aprovar_agendamento', args=[self.a1.pk]))
        self.client.post(reverse('aranha:admin_bulk_agendamentos'), {'ids': [str(self.a2.pk)], 'acao': 'rejeitar'})
        self.assertEqual(self._ip('Aprovou agendamento', self.a1), '127.0.0.1')
        self.assertEqual(self._ip('Rejeitou agendamento', self.a2), '127.0.0.1')


# ─── Previa de e-mails ────────────────────────────────────────────────
class EmailPreviewTests(_AdminTestCase):
    def _get(self, nome):
        return self.client.get(reverse('aranha:admin_email_preview_nome', args=[nome]))

    def test_todas_renderizam_com_nonce_e_links(self):
        from aranha_estetica.views.admin_management import EMAIL_PREVIEW_FIXTURES
        for nome in EMAIL_PREVIEW_FIXTURES:
            resp = self._get(nome)
            self.assertEqual(resp.status_code, 200, nome)
            html = resp.content.decode()
            self.assertNotIn('href=""', html, nome)
            m = re.search(r"'nonce-([^']+)'", resp.get('Content-Security-Policy', ''))
            if m and '<style' in html:
                self.assertEqual(re.findall(r'<style(?![^>]*nonce=)', html), [], nome)
                self.assertIn(f'nonce="{m.group(1)}"', html, nome)

    def test_promocao_e_aniversario_como_no_envio_real(self):
        html = self._get('promocao').content.decode()
        for trecho in ('Maria Silva', 'Oferta especial do mês', '30/05/2026'):
            self.assertIn(trecho, html)
        self.assertNotIn('Presente de aniversario', self._get('aniversario').content.decode())


# ─── Valores com teto (sem 500) ───────────────────────────────────────
class ValorComTetoTests(_AdminTestCase):
    def test_parser_recusa_magnitude_e_lixo(self):
        from aranha_estetica.views.admin_management import _parse_preco
        for valor in ('1e30', '1e26', '9' * 30, '123456789', 'NaN', 'inf', '-1', 'abc'):
            with self.assertRaises(ValueError, msg=valor):
                _parse_preco(valor)
        self.assertEqual(_parse_preco('99999999.99'), Decimal('99999999.99'))
        self.assertEqual(_parse_preco('1350,5'), Decimal('1350.50'))
        self.assertIsNone(_parse_preco('  '))

    def test_rotas_de_valor_nao_dao_500(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cli = criar_cliente('Valor Grande')
        at = criar_atendimento(cli, prof, proc, status='REALIZADO')
        pacote = criar_pacote('Pacote Teto', procedimento=proc)
        for valor in ('1e30', '123456789'):
            resp = self.client.post(reverse('aranha:admin_atendimento_valor', args=[at.pk]), {'valor': valor})
            self.assertEqual(resp.status_code, 302)
            self.assertTrue(any('Valor inválido' in m for m in _mensagens(resp)), valor)

            resp = self.client.post(reverse('aranha:admin_agendamento_novo'), {
                'acao': 'agendar', 'cliente_id': str(cli.pk), 'procedimento_id': str(proc.pk),
                'profissional_id': str(prof.pk), 'data': _local(3).date().isoformat(), 'hora': '10:00',
                'valor': valor,
            })
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(any('Valor inválido' in m for m in _mensagens(resp)), valor)

            resp = self.client.post(reverse('aranha:admin_criar_procedimento'),
                                    {'nome': f'Proc {valor}', 'duracao_minutos': '30', 'preco': valor})
            self.assertEqual(resp.status_code, 302)
            resp = self.client.post(reverse('aranha:admin_criar_pacote'),
                                    {'nome': f'Pac {valor}', 'preco_total': valor, 'validade_meses': '12'})
            self.assertEqual(resp.status_code, 302)
            resp = self.client.post(reverse('aranha:admin_vender_pacote'),
                                    {'pacote_id': str(pacote.pk), 'cliente_id': str(cli.pk), 'valor_pago': valor})
            self.assertEqual(resp.status_code, 302)
        at.refresh_from_db()
        self.assertIsNone(at.valor_cobrado)
        self.assertFalse(Atendimento.objects.filter(cliente=cli).exclude(pk=at.pk).exists())
        self.assertFalse(Procedimento.objects.filter(nome__startswith='Proc ').exists())
        self.assertFalse(Pacote.objects.filter(nome__startswith='Pac ').exists())
        self.assertFalse(CompraPacote.objects.exists())


# ─── Pacote cancelado: reembolso estruturado ──────────────────────────
class PacoteCanceladoReembolsoTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.cli = criar_cliente('Fernanda Rastreavel')
        self.compra = criar_compra_pacote(self.cli, criar_pacote('Pacote Glow', preco=Decimal('1000.00')))
        self.url = reverse('aranha:admin_cancelar_compra_pacote', args=[self.compra.pk])

    def test_exige_reembolso_ate_o_valor_pago(self):
        for extra in ({}, {'valor_reembolsado': 'abc'}, {'valor_reembolsado': '1000,01'}):
            self.client.post(self.url, {'observacao': 'Desistiu', **extra})
            self.compra.refresh_from_db()
            self.assertEqual(self.compra.status, 'ATIVO', extra)
        self.client.post(self.url, {'observacao': 'Devolvemos via PIX', 'valor_reembolsado': '700,00'})
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.status, 'CANCELADO')
        log = LogAuditoria.objects.get(tabela='compra_pacote', registro_id=self.compra.pk)
        self.assertEqual(log.detalhes['valor_reembolsado'], '700.00')
        self.assertEqual(log.detalhes['valor_pago'], '1000.00')
        self.assertNotIn('Fernanda', log.acao)

    def test_venda_nao_grava_nome_da_cliente_no_log(self):
        """rev_security-07: 'Vendeu pacote ... para <nome>' sobrevivia ao esquecimento."""
        pacote = criar_pacote('Pacote Novo')
        self.client.post(reverse('aranha:admin_vender_pacote'),
                         {'pacote_id': str(pacote.pk), 'cliente_id': str(self.cli.pk), 'valor_pago': '600'})
        log = LogAuditoria.objects.get(acao__startswith='Vendeu pacote')
        self.assertNotIn('Fernanda', log.acao)
        self.assertEqual(log.detalhes['cliente'], self.cli.pk)


# ─── Promocoes: global e preco fixo editaveis ─────────────────────────
class PromocaoEdicaoTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.proc = criar_procedimento()
        self.hoje = timezone.localdate()

    def _post(self, promo, **extra):
        data = {'nome': promo.nome, 'descricao': '', 'ativa': '0',
                'data_inicio': promo.data_inicio.isoformat(), 'data_fim': promo.data_fim.isoformat()}
        data.update(extra)
        return self.client.post(reverse('aranha:admin_editar_promocao', args=[promo.pk]), data)

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
        resp = self._post(promo, procedimento=str(self.proc.pk), preco_promocional='99.00')
        promo.refresh_from_db()
        self.assertFalse(promo.ativa)
        self.assertEqual(promo.preco_promocional, Decimal('99.00'))
        self.assertEqual(promo.desconto_percentual, Decimal('0'))
        self.assertFalse(any('Erro' in m for m in _mensagens(resp)))


# ─── Agendamento interno: cliente inativa ─────────────────────────────
class ClienteInativaAgendamentoTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.inativa = criar_cliente('Julia Inativa', telefone='17988887777', ativo=False)
        self.url = reverse('aranha:admin_agendamento_novo')
        self.ficha = reverse('aranha:admin_cliente_detalhe', args=[self.inativa.pk])

    def test_atalho_da_ficha_avisa_e_leva_para_reativar(self):
        resp = self.client.get(self.url, {'cliente': self.inativa.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any('cadastro inativo' in m for m in _mensagens(resp)))
        self.assertContains(resp, self.ficha)

    def test_busca_mostra_inativa_com_link(self):
        resp = self.client.post(self.url, {'acao': 'buscar', 'q': '17988887777'})
        self.assertContains(resp, 'Julia Inativa')
        self.assertContains(resp, 'Reativar na ficha')
        self.assertContains(resp, self.ficha)

    def test_cadastro_novo_com_telefone_da_inativa(self):
        resp = self.client.post(self.url, {
            'acao': 'agendar', 'cliente_id': '', 'novo_nome': 'Julia', 'novo_telefone': '(17) 98888-7777',
            'procedimento_id': str(self.proc.pk), 'profissional_id': str(self.prof.pk),
            'data': _local(3).date().isoformat(), 'hora': '10:00',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any('cadastro inativo' in m for m in _mensagens(resp)))
        self.assertContains(resp, self.ficha)
        self.assertFalse(Atendimento.objects.exists())

    def test_agendar_com_id_da_inativa_nao_grava_nem_quebra(self):
        resp = self.client.post(self.url, {
            'acao': 'agendar', 'cliente_id': str(self.inativa.pk),
            'procedimento_id': str(self.proc.pk), 'profissional_id': str(self.prof.pk),
            'data': _local(3).date().isoformat(), 'hora': '10:00',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Atendimento.objects.exists())

    def test_ficha_da_inativa_nao_oferece_agendar(self):
        resp = self.client.get(self.ficha)
        self.assertNotContains(resp, f'{self.url}?cliente={self.inativa.pk}')
        self.assertContains(resp, 'Cadastro inativo')


# ─── Valor registrado depois do REALIZADO libera o cashback ───────────
class CashbackAoRegistrarValorTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=None)  # "a consultar"
        self.indicadora = criar_cliente('Indicadora')
        self.indicada = criar_cliente('Indicada', indicado_por=self.indicadora)

    def _cashbacks(self, at):
        return MovimentoCarteira.objects.filter(origem='CASHBACK_INDICACAO', atendimento=at)

    def test_registrar_valor_credita_indicadora_uma_vez(self):
        at = criar_atendimento(self.indicada, self.prof, self.proc, status='REALIZADO')
        url = reverse('aranha:admin_atendimento_valor', args=[at.pk])
        self.client.post(url, {'valor': '800,00'})
        self.assertEqual(self._cashbacks(at).count(), 1)
        # 2o registro: idempotente e o valor novo continua gravado
        self.client.post(url, {'valor': '900,00'})
        at.refresh_from_db()
        self.assertEqual(at.valor_cobrado, Decimal('900.00'))
        self.assertEqual(self._cashbacks(at).count(), 1)

    def test_nao_e_o_primeiro_pago(self):
        anterior = criar_atendimento(self.indicada, self.prof, self.proc, data_hora=_local(-10, 10),
                                     status='REALIZADO')
        Atendimento.objects.filter(pk=anterior.pk).update(valor_cobrado=Decimal('100.00'))
        at = criar_atendimento(self.indicada, self.prof, self.proc, status='REALIZADO')
        self.client.post(reverse('aranha:admin_atendimento_valor', args=[at.pk]), {'valor': '800,00'})
        self.assertFalse(MovimentoCarteira.objects.filter(origem='CASHBACK_INDICACAO').exists())


# ─── Ficha de anamnese obrigatoria pendente ───────────────────────────
class FichaPendenteTests(_AdminTestCase):
    MARCA = 'Ficha de anamnese pendente'

    def setUp(self):
        super().setUp()
        self.prof = criar_profissional('Dra. Ficha')
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente('Cliente Telefone')
        self.form = FormularioAnamnese.objects.create(
            nome='Ficha facial', tipo='ANAMNESE', escopo='GLOBAL', obrigatorio=True,
            schema_json=[{'key': 'alergias', 'tipo': 'text', 'label': 'Possui alergias?'}],
        )

    def test_agendamento_interno_avisa_e_listas_marcam(self):
        resp = self.client.post(reverse('aranha:admin_agendamento_novo'), {
            'acao': 'agendar', 'cliente_id': str(self.cli.pk), 'procedimento_id': str(self.proc.pk),
            'profissional_id': str(self.prof.pk), 'data': _local(2).date().isoformat(), 'hora': '10:00',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(any('Ficha de anamnese obrigatória pendente' in m for m in _mensagens(resp)))
        self.assertContains(self.client.get(reverse('aranha:painel_agendamentos')), self.MARCA)

        self.client.force_login(_prof_usuario(self.prof, 'prof-ficha@test.com'))
        resp = self.client.get(reverse('aranha:profissional_agenda'), {'data': _local(2).date().isoformat()})
        self.assertContains(resp, self.MARCA)

        RespostaAnamnese.objects.create(formulario=self.form, cliente=self.cli,
                                        respostas_json={'alergias': 'nao'}, respondida_em=timezone.now())
        resp = self.client.get(reverse('aranha:profissional_agenda'), {'data': _local(2).date().isoformat()})
        self.assertNotContains(resp, self.MARCA)

    def test_regra_do_marcador(self):
        from aranha_estetica.services.alertas import ids_com_ficha_pendente
        ativo = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(2, 10))
        cancelado = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(2, 15), status='CANCELADO')
        self.assertEqual(ids_com_ficha_pendente([ativo, cancelado]), {ativo.pk})
        FormularioAnamnese.objects.filter(pk=self.form.pk).update(obrigatorio=False)
        self.assertEqual(ids_com_ficha_pendente([ativo]), set())


# ─── Lista de espera: nome so p/ o e-mail do proprio cadastro ─────────
class ListaEsperaNomeTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.proc = criar_procedimento()
        self.vitima = criar_cliente('Valeria Vitima', email='vitima@x.test')

    def _avisar(self, email_contato):
        item = ListaEspera.objects.create(cliente=self.vitima, procedimento=self.proc,
                                          data_desejada=timezone.localdate() + timedelta(days=3),
                                          email_contato=email_contato)
        self.client.post(reverse('aranha:admin_notificar_espera', args=[item.pk]))
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        return msg, msg.body + ''.join(c for c, _t in getattr(msg, 'alternatives', []))

    def test_email_digitado_por_terceiro_nao_recebe_o_nome(self):
        msg, conteudo = self._avisar('atacante@x.test')
        self.assertEqual(msg.to, ['atacante@x.test'])
        self.assertNotIn('Valeria', conteudo)

    def test_email_do_cadastro_recebe_o_nome(self):
        msg, conteudo = self._avisar(None)
        self.assertEqual(msg.to, ['vitima@x.test'])
        self.assertIn('Valeria', conteudo)


# ─── Templates: popover no celular, reenvio com termo, SRI, extras ────
class TemplatesPainelTests(TestCase):
    def test_popover_de_valor_entra_no_fluxo_no_celular(self):
        html = (TEMPLATES / 'painel' / 'agendamentos.html').read_text(encoding='utf-8')
        self.assertIn('static sm:absolute sm:right-0', html)
        self.assertNotIn('class="absolute right-0', html)

    def test_status_reenvia_com_override_quando_servidor_pede_termo(self):
        html = (TEMPLATES / 'painel' / 'agendamentos.html').read_text(encoding='utf-8')
        self.assertIn('data.termo_pendente', html)
        self.assertIn('payload.sem_termo = true', html)

    def test_calendario_com_sri(self):
        html = (TEMPLATES / 'painel' / 'calendar.html').read_text(encoding='utf-8')
        scripts = re.findall(r'<script src="https://cdn\.jsdelivr\.net/[^>]*>', html)
        self.assertEqual(len(scripts), 2)
        for tag in scripts:
            self.assertIn('integrity="sha384-', tag)
            self.assertIn('crossorigin="anonymous"', tag)

    def test_alerta_em_lote_considera_respostas_extras(self):
        from aranha_estetica.services.alertas import alertas_por_cliente
        cli = criar_cliente('Gestante Migrada')
        Prontuario.objects.create(cliente=cli, respostas_extras={'esta_gravida_ou_amamentando': True})
        self.assertIn(cli.pk, alertas_por_cliente([cli]))


# ─── Postgres: EXCLUDE real no agendamento interno (sem mock) ─────────
@skipUnless(connection.vendor == 'postgresql', 'EXCLUDE excl_atendimento_sobreposicao so existe no Postgres')
class AgendamentoInternoExcludePgTests(_AdminTestCase):
    def test_corrida_de_slot_vira_mensagem_e_nao_500(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        dia = _local(3).date()
        criar_atendimento(criar_cliente('Ocupa'), prof, proc,
                          data_hora=timezone.make_aware(datetime.combine(dia, time(10, 0))))
        cli = criar_cliente('Perdeu a corrida')
        # pre-checagem do app "perde" a corrida: so o EXCLUDE do banco segura
        with mock.patch('aranha_estetica.views.admin_agendamento.slot_disponivel', return_value=True):
            resp = self.client.post(reverse('aranha:admin_agendamento_novo'), {
                'acao': 'agendar', 'cliente_id': str(cli.pk), 'procedimento_id': str(proc.pk),
                'profissional_id': str(prof.pk), 'data': dia.isoformat(), 'hora': '10:00',
            })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any('acabou de ser ocupado' in m for m in _mensagens(resp)))
        self.assertFalse(Atendimento.objects.filter(cliente=cli).exists())
