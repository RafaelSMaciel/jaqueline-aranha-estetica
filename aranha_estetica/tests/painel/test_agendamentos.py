"""Agenda do painel (P06): select de status pelos casos de uso, novo agendamento por Forms, valor
pelo service e os marcadores da lista calculados na view."""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.excecoes import RegraDeNegocio
from aranha_estetica.models import Atendimento, Cliente, LogAuditoria, Notificacao, Usuario, VersaoTermo

from ..factories import (
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional, ja_comecou, limpar_feriados,
)


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class _Painel(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email='admin-agenda-p06@test.com', password='senha-forte-123', nome='Admin Agenda',
        )
        self.client.force_login(self.admin)
        self.prof = criar_profissional('Dra. Agenda')
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente('Carla Status')


# ─── Select de status (admin_atualizar_status) ────────────────────────
class SelectDeStatusTests(_Painel):
    def _status(self, at, status, **extra):
        return self.client.post(
            reverse('aranha:admin_atualizar_status'),
            data=json.dumps({'atendimento_id': at.pk, 'status': status, **extra}), content_type='application/json',
        )

    def test_realizado_antes_do_horario_da_409_com_a_mensagem(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, data_hora=timezone.now() + timedelta(days=8))
        resp = self._status(at, 'REALIZADO')
        self.assertEqual(resp.status_code, 409)
        corpo = resp.json()
        self.assertEqual((corpo['ok'], corpo['codigo']), (False, 'desfecho_antes_do_inicio'))
        self.assertIn('depois do início', corpo['mensagem'])
        at.refresh_from_db()
        self.assertEqual(at.status, 'AGENDADO')

    def test_falta_antes_do_horario_tambem_recusa(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, data_hora=timezone.now() + timedelta(days=8))
        self.assertEqual(self._status(at, 'FALTOU').status_code, 409)

    def test_agendado_fora_de_pedido_e_400_e_status_desconhecido_tambem(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, status='CONFIRMADO')
        for status in ('AGENDADO', 'REAGENDADO', 'XYZ'):
            with self.subTest(status=status):
                resp = self._status(at, status)
                self.assertEqual(resp.status_code, 400)
                self.assertFalse(resp.json()['ok'])
        at.refresh_from_db()
        self.assertEqual(at.status, 'CONFIRMADO')

    def test_realizado_com_termo_pendente_pede_override_e_o_service_audita(self):
        termo = VersaoTermo.objects.create(
            tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo', conteudo='Riscos.', versao='1.0',
            vigente_desde=timezone.localdate(),
        )
        at = ja_comecou(criar_atendimento(self.cli, self.prof, self.proc))
        resp = self._status(at, 'REALIZADO')
        self.assertEqual((resp.status_code, resp.json()['codigo'], resp.json()['termos']),
                         (409, 'termo_pendente', [termo.pk]))
        resp = self._status(at, 'REALIZADO', sem_termo=True)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json(), {'ok': True, 'status_anterior': 'AGENDADO', 'status_novo': 'REALIZADO'})
        self.assertTrue(LogAuditoria.objects.filter(acao='Realizado sem termo aceito', registro_id=at.pk).exists())
        self.assertTrue(LogAuditoria.objects.filter(acao='Status alterado: AGENDADO → REALIZADO').exists())

    def test_get_nao_altera(self):
        self.assertEqual(self.client.get(reverse('aranha:admin_atualizar_status')).status_code, 405)


class PortalUsaOMesmoCasoDeUsoTests(_Painel):
    def test_profissional_com_termo_pendente_e_override(self):
        VersaoTermo.objects.create(tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo', conteudo='R.',
                                   versao='1.0', vigente_desde=timezone.localdate())
        user = Usuario.objects.create_user(email='prof-p06@test.com', password='senha-forte-123', nome='Dra.',
                                           papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof)
        self.client.force_login(user)
        at = ja_comecou(criar_atendimento(self.cli, self.prof, self.proc))
        url = reverse('aranha:profissional_marcar_realizado', args=[at.pk])
        resp = self.client.post(url)
        self.assertTrue(any('ainda não aceitou o termo' in m for m in _mensagens(resp)))
        at.refresh_from_db()
        self.assertEqual(at.status, 'AGENDADO')
        self.client.post(url, {'sem_termo': '1'})
        at.refresh_from_db()
        self.assertEqual(at.status, 'REALIZADO')
        log = LogAuditoria.objects.get(acao='Realizado sem termo aceito')
        self.assertEqual(log.usuario_id, user.pk)


# ─── Lista da agenda: select so quando vale, marcadores e retorno ─────
class ListaDaAgendaTests(_Painel):
    def _lista(self, **params):
        resp = self.client.get(reverse('aranha:painel_agendamentos'), params)
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    @staticmethod
    def _select(html, at):
        trecho = html[html.index(f'data-atend-id="{at.pk}"'):]
        return trecho[:trecho.index('</select>')]

    def test_pendente_sem_select_e_com_aprovar(self):
        pendente = criar_atendimento(self.cli, self.prof, self.proc, status='PENDENTE')
        html = self._lista()
        self.assertNotIn(f'data-atend-id="{pendente.pk}"', html)
        self.assertIn(reverse('aranha:admin_aprovar_agendamento', args=[pendente.pk]), html)

    def test_realizado_e_faltou_so_a_partir_do_inicio(self):
        semana_que_vem = criar_atendimento(self.cli, self.prof, self.proc,
                                           data_hora=timezone.now() + timedelta(days=8))
        em_curso = ja_comecou(criar_atendimento(criar_cliente('Em curso'), self.prof, self.proc))
        html = self._lista()
        futuro = self._select(html, semana_que_vem)
        self.assertNotIn('value="REALIZADO"', futuro)
        self.assertNotIn('value="FALTOU"', futuro)
        self.assertIn('value="CANCELADO"', futuro)
        self.assertIn(f'data-url="{reverse("aranha:admin_atualizar_status")}"', futuro)
        atual = self._select(html, em_curso)
        self.assertIn('value="REALIZADO"', atual)
        self.assertIn('value="FALTOU"', atual)

    def test_status_final_sem_select(self):
        realizado = criar_atendimento(self.cli, self.prof, self.proc, status='REALIZADO')
        self.assertNotIn(f'data-atend-id="{realizado.pk}"', self._lista())

    def test_alerta_de_saude_entra_na_trilha_de_leitura(self):
        from aranha_estetica.models import Prontuario
        Prontuario.objects.create(cliente=self.cli, alergias='Látex')
        criar_atendimento(self.cli, self.prof, self.proc)
        criar_atendimento(criar_cliente('Sem Alerta'), self.prof, self.proc,
                          data_hora=timezone.now() + timedelta(days=3))
        self.assertIn('Látex', self._lista())
        log = LogAuditoria.objects.get(acao='Leu alertas de saúde')
        self.assertEqual(log.detalhes, {'clientes': [self.cli.pk], 'tela': 'agendamentos'})
        self.assertEqual((log.tabela, log.usuario_id), ('prontuario', self.admin.pk))
        self.assertNotIn('Carla', log.acao)

    def test_sem_alerta_nao_grava_leitura(self):
        criar_atendimento(self.cli, self.prof, self.proc)
        self._lista()
        self.assertFalse(LogAuditoria.objects.filter(acao='Leu alertas de saúde').exists())

    def test_retorno_devido_ganha_botao_e_some_quando_marcado(self):
        from aranha_estetica.models import Procedimento
        Procedimento.objects.filter(pk=self.proc.pk).update(exige_retorno=True, retorno_minimo_dias=7,
                                                             retorno_maximo_dias=30)
        origem = criar_atendimento(self.cli, self.prof, self.proc, data_hora=timezone.now() - timedelta(days=2),
                                   status='REALIZADO')
        url = reverse('aranha:admin_agendar_retorno', args=[origem.pk])
        html = self._lista()
        self.assertIn(url, html)
        self.assertIn('Agendar retorno (até', html)
        inicio = timezone.now() + timedelta(days=10)
        Atendimento.objects.create(cliente=self.cli, profissional=self.prof, procedimento=self.proc,
                                   data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=30),
                                   status='AGENDADO', eh_retorno=True, atendimento_origem=origem)
        self.assertNotIn(url, self._lista())


# ─── Novo agendamento (Forms + agenda.agendar_pela_recepcao) ──────────
class NovoAgendamentoTests(_Painel):
    def setUp(self):
        super().setUp()
        limpar_feriados()
        self.url = reverse('aranha:admin_agendamento_novo')
        self.dia = (timezone.localdate() + timedelta(days=3)).isoformat()

    def _agendar(self, **campos):
        dados = {'acao': 'agendar', 'procedimento_id': str(self.proc.pk), 'profissional_id': str(self.prof.pk),
                 'data': self.dia, 'hora': '10:00', **campos}
        return self.client.post(self.url, dados)

    def test_cliente_nova_com_mais_55_e_confirmacao(self):
        resp = self._agendar(cliente_id='', novo_nome=' Lia Nova ', novo_telefone='+55 (17) 99123-4567',
                             novo_email='LIA@Exemplo.test')
        self.assertEqual(resp.status_code, 302, _mensagens(resp))
        cliente = Cliente.objects.get(telefone='17991234567')
        self.assertEqual((cliente.nome, cliente.email), ('Lia Nova', 'lia@exemplo.test'))
        at = Atendimento.objects.get(cliente=cliente)
        self.assertEqual((at.status, at.valor_cobrado, at.descricao_preco), ('AGENDADO', Decimal('150.00'), ''))
        self.assertEqual(Notificacao.objects.filter(atendimento=at, tipo='CONFIRMACAO', canal='EMAIL').count(), 1)
        log = LogAuditoria.objects.get(acao__startswith='Agendou pelo painel')
        self.assertEqual((log.registro_id, log.detalhes['cliente_novo']), (at.pk, True))

    def test_valor_combinado_grava_a_descricao_e_o_cheio(self):
        resp = self._agendar(cliente_id=str(self.cli.pk), valor='120,50')
        self.assertEqual(resp.status_code, 302, _mensagens(resp))
        at = Atendimento.objects.get(cliente=self.cli)
        self.assertEqual((at.valor_cobrado, at.valor_original, at.descricao_preco),
                         (Decimal('120.50'), Decimal('150.00'), 'Valor combinado pela recepção'))

    def test_valor_igual_ao_do_dia_nao_vira_combinado(self):
        self._agendar(cliente_id=str(self.cli.pk), valor='150,00')
        at = Atendimento.objects.get(cliente=self.cli)
        self.assertEqual((at.valor_original, at.descricao_preco), (None, ''))

    def test_telefone_sem_digitos_e_dono_ativo_recusam(self):
        resp = self._agendar(cliente_id='', novo_nome='Sem Fone', novo_telefone='abc')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Telefone inválido: informe DDD + número (10 ou 11 dígitos).', _mensagens(resp))
        criar_cliente('Dona Ativa', telefone='17988880000')
        resp = self._agendar(cliente_id='', novo_nome='Outra', novo_telefone='(17) 98888-0000')
        self.assertIn('Este telefone já é de Dona Ativa: busque e selecione a cliente cadastrada.', _mensagens(resp))
        self.assertFalse(Atendimento.objects.exists())

    def test_email_de_inativa_leva_a_ficha(self):
        inativa = criar_cliente('Ines Inativa', email='ines@x.test', ativo=False)
        resp = self._agendar(cliente_id='', novo_nome='Ines', novo_telefone='17977776666', novo_email='INES@x.test')
        self.assertTrue(any('Este e-mail é de Ines Inativa, com cadastro inativo' in m for m in _mensagens(resp)))
        self.assertContains(resp, reverse('aranha:admin_cliente_detalhe', args=[inativa.pk]))
        self.assertFalse(Cliente.objects.filter(telefone='17977776666').exists())

    def test_sem_cliente_e_campos_faltando(self):
        resp = self.client.post(self.url, {'acao': 'agendar'})
        msgs = _mensagens(resp)
        self.assertIn('Selecione a cliente na busca ou preencha o cadastro da cliente nova.', msgs)
        self.assertIn('Selecione o procedimento.', msgs)
        self.assertIn('Escolha a data e um dos horários livres.', msgs)
        self.assertEqual(msgs.count('Escolha a data e um dos horários livres.'), 1)   # data e hora: 1 aviso

    def test_ids_manipulados_nao_quebram(self):
        for bruto in ('9' * 30, '²', '-1', 'abc'):
            with self.subTest(bruto=bruto):
                resp = self._agendar(cliente_id=str(self.cli.pk), procedimento_id=bruto)
                self.assertEqual(resp.status_code, 200)
                self.assertIn('Selecione o procedimento.', _mensagens(resp))
        self.assertEqual(self.client.get(self.url, {'cliente': '9' * 30, 'procedimento_id': '²'}).status_code, 200)

    def test_horario_recusado_traz_o_motivo(self):
        resp = self._agendar(cliente_id=str(self.cli.pk), hora='23:00')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any('Fora do horário' in m for m in _mensagens(resp)))
        self.assertFalse(Atendimento.objects.exists())

    def test_etapas_intermediarias_nao_validam(self):
        resp = self.client.post(self.url, {'acao': 'horarios', 'procedimento_id': str(self.proc.pk),
                                           'profissional_id': str(self.prof.pk), 'data': self.dia})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_mensagens(resp), [])
        self.assertTrue(resp.context['viu_horarios'])
        self.assertIn('10:00', resp.context['slots'])
        self.assertEqual(resp.context['preco']['valor_cobrado'], Decimal('150.00'))


# ─── Valor cobrado (services.atendimento.registrar_valor_cobrado) e desfazer ───
class ValorEDesfazerTests(_Painel):
    def test_retorno_e_status_final_recusam_pelo_service(self):
        from aranha_estetica.services.atendimento import registrar_valor_cobrado
        cancelado = criar_atendimento(self.cli, self.prof, self.proc, status='CANCELADO')
        with self.assertRaisesMessage(RegraDeNegocio, 'Atendimento cancelado: o valor não pode ser alterado.'):
            registrar_valor_cobrado(cancelado, Decimal('10'))
        origem = criar_atendimento(self.cli, self.prof, self.proc, status='REALIZADO',
                                   data_hora=timezone.now() - timedelta(days=5))
        Atendimento.objects.filter(pk=cancelado.pk).update(status='AGENDADO', eh_retorno=True,
                                                           atendimento_origem=origem)
        retorno = Atendimento.objects.get(pk=cancelado.pk)
        with self.assertRaisesMessage(RegraDeNegocio, 'Retorno gratuito fica sempre com valor zero.'):
            registrar_valor_cobrado(retorno, Decimal('10'))
        self.assertIsNone(registrar_valor_cobrado(retorno, Decimal('0')).antigo)

    def test_view_registra_trilha_e_vazio_e_invalido(self):
        at = criar_atendimento(self.cli, self.prof, self.proc)
        url = reverse('aranha:admin_atendimento_valor', args=[at.pk])
        resp = self.client.post(url, {'valor': ''})
        self.assertIn('Valor inválido: use números, ex.: 150,00.', _mensagens(resp))
        resp = self.client.post(url, {'valor': '99,90'}, HTTP_REFERER='/painel/agendamentos/?data=2030-01-01')
        self.assertRedirects(resp, '/painel/agendamentos/?data=2030-01-01', fetch_redirect_response=False)
        at.refresh_from_db()
        self.assertEqual((at.valor_cobrado, at.descricao_preco), (Decimal('99.90'), 'Valor combinado pela recepção'))
        log = LogAuditoria.objects.get(acao='Registrou valor cobrado')
        self.assertEqual(log.detalhes['para'], '99.90')

    def test_desfazer_realizacao_continua(self):
        at = ja_comecou(criar_atendimento(self.cli, self.prof, self.proc))
        at.marcar_realizado()
        url = reverse('aranha:admin_desfazer_realizacao', args=[at.pk])
        resp = self.client.post(url, {'destino': 'XYZ', 'motivo': 'engano'})
        self.assertIn('Escolha para onde o atendimento volta: agendado, faltou ou cancelado.', _mensagens(resp))
        resp = self.client.post(url, {'destino': 'faltou', 'motivo': 'marcado errado'})
        self.assertIn('Realização de Carla Status desfeita: agora faltou.', _mensagens(resp))
        at.refresh_from_db()
        self.assertEqual(at.status, 'FALTOU')
        self.assertEqual(self.client.get(url).status_code, 405)
