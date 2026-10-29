"""Agenda do painel (P06): select de status pelos casos de uso, novo agendamento por Forms, valor
pelo service e os marcadores da lista calculados na view."""
import json
from datetime import timedelta

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import LogAuditoria, Usuario, VersaoTermo

from ..factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional, ja_comecou


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
        from aranha_estetica.models import Atendimento, Procedimento
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
