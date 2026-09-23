"""Regressao do painel operacional (P5): profissionais, agendamentos, portal do
profissional, termos, clientes, precos, pacotes, promocoes, lista de espera,
calendario, excecoes e Google Calendar."""
from datetime import time, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core import mail
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import (
    Atendimento,
    BloqueioAgenda,
    Cliente,
    CompraPacote,
    Configuracao,
    DisponibilidadeProfissional,
    ExcecaoDisponibilidade,
    Habilitacao,
    ListaEspera,
    LogAuditoria,
    MovimentoComissao,
    Preco,
    Profissional,
    Promocao,
    RegraComissao,
    Usuario,
    VersaoTermo,
)

from .factories import (
    criar_atendimento,
    criar_cliente,
    criar_pacote,
    criar_procedimento,
    criar_profissional,
)


def _mensagens(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


class _FormNesting(HTMLParser):
    """Conta a profundidade maxima de <form> (form aninhado e HTML invalido)."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.max_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'form':
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)

    def handle_endtag(self, tag):
        if tag == 'form':
            self.depth -= 1


class _AdminTestCase(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email='admin-p5@test.com', password='senha-forte-123', nome='Admin P5',
        )
        self.client.force_login(self.admin)


# ─── Profissionais: editar/cadastrar ──────────────────────────────────
class EditarProfissionalTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.prof = criar_profissional('Dra. Bia')  # 7 dias 09:00-18:00
        self.proc = criar_procedimento(profissional=self.prof)
        self.url = reverse('aranha:profissional_editar', args=[self.prof.pk])

    def _post_roundtrip(self, **extra):
        data = {'nome': self.prof.nome, 'especialidade': 'Estética', 'ativo': '1',
                'procedimentos': [str(self.proc.pk)]}
        for key in ('segunda', 'terca', 'quarta', 'quinta', 'sexta', 'sabado', 'domingo'):
            data[f'trabalha_{key}'] = 'on'
            data[f'hora_inicio_{key}'] = '09:00'
            data[f'hora_fim_{key}'] = '18:00'
        data.update(extra)
        return self.client.post(self.url, data)

    def test_get_renderiza_horarios(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="hora_inicio_segunda"')
        self.assertContains(resp, 'value="09:00"')
        self.assertContains(resp, 'value="18:00"')

    def test_post_ida_e_volta_preserva_agenda_e_ativo(self):
        resp = self._post_roundtrip()
        self.assertEqual(resp.status_code, 302)
        self.prof.refresh_from_db()
        self.assertTrue(self.prof.ativo)
        self.assertEqual(DisponibilidadeProfissional.objects.filter(profissional=self.prof).count(), 7)
        self.assertTrue(Habilitacao.objects.filter(profissional=self.prof, procedimento=self.proc).exists())

    def test_sem_ativo_desativa(self):
        resp = self.client.post(self.url, {'nome': self.prof.nome})
        self.assertEqual(resp.status_code, 302)
        self.prof.refresh_from_db()
        self.assertFalse(self.prof.ativo)

    def test_hora_fim_antes_do_inicio_nao_apaga_nada(self):
        resp = self._post_roundtrip(hora_inicio_segunda='14:00', hora_fim_segunda='09:00')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any('hora de fim' in m for m in _mensagens(resp)))
        self.assertEqual(DisponibilidadeProfissional.objects.filter(profissional=self.prof).count(), 7)

    def test_dia_marcado_sem_hora_nao_apaga_nada(self):
        resp = self._post_roundtrip(hora_inicio_terca='', hora_fim_terca='')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(DisponibilidadeProfissional.objects.filter(profissional=self.prof).count(), 7)

    def test_turno_dividido_preservado_se_horario_nao_muda(self):
        # segunda (dia_semana=2) em turno dividido: 09:00-12:00 + 14:00-18:00
        DisponibilidadeProfissional.objects.filter(profissional=self.prof, dia_semana=2).update(
            hora_fim=time(12, 0))
        DisponibilidadeProfissional.objects.create(
            profissional=self.prof, dia_semana=2, hora_inicio=time(14, 0), hora_fim=time(18, 0))
        resp = self._post_roundtrip(hora_inicio_segunda='09:00', hora_fim_segunda='12:00')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(DisponibilidadeProfissional.objects.filter(profissional=self.prof, dia_semana=2).count(), 2)

    def test_habilitacao_de_procedimento_inativo_preservada(self):
        inativo = criar_procedimento(nome='Antigo', profissional=self.prof)
        inativo.ativo = False
        inativo.save()
        self._post_roundtrip()
        self.assertTrue(Habilitacao.objects.filter(profissional=self.prof, procedimento=inativo).exists())


class CadastrarProfissionalTests(_AdminTestCase):
    def test_checkbox_ativo_1_cria_ativo(self):
        resp = self.client.post(reverse('aranha:profissional_cadastro'), {
            'nome': 'Nova Prof', 'ativo': '1',
            'trabalha_segunda': 'on', 'hora_inicio_segunda': '09:00', 'hora_fim_segunda': '17:00',
        })
        self.assertEqual(resp.status_code, 302)
        prof = Profissional.objects.get(nome='Nova Prof')
        self.assertTrue(prof.ativo)
        self.assertEqual(prof.disponibilidadeprofissional_set.count(), 1)

    def test_sem_ativo_cria_inativo(self):
        self.client.post(reverse('aranha:profissional_cadastro'), {'nome': 'Prof Off'})
        self.assertFalse(Profissional.objects.get(nome='Prof Off').ativo)

    def test_get_200(self):
        self.assertEqual(self.client.get(reverse('aranha:profissional_cadastro')).status_code, 200)


# ─── Agendamentos: forms, lote via FSM, filtros ───────────────────────
class AgendamentosPainelTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        base = timezone.now() + timedelta(days=2)
        self.a1 = criar_atendimento(criar_cliente('Ana'), self.prof, self.proc,
                                    data_hora=base.replace(hour=10, minute=0, second=0, microsecond=0),
                                    status='PENDENTE')
        self.a2 = criar_atendimento(criar_cliente('Bia'), self.prof, self.proc,
                                    data_hora=base.replace(hour=14, minute=0, second=0, microsecond=0),
                                    status='PENDENTE')

    def test_sem_form_aninhado(self):
        resp = self.client.get(reverse('aranha:painel_agendamentos'))
        self.assertEqual(resp.status_code, 200)
        parser = _FormNesting()
        parser.feed(resp.content.decode())
        self.assertEqual(parser.max_depth, 1)
        self.assertContains(resp, 'form="bulkForm"')
        self.assertNotContains(resp, '{#')  # comentario de template vazando no HTML

    def test_bulk_aprovar_usa_fsm(self):
        resp = self.client.post(reverse('aranha:admin_bulk_agendamentos'), {
            'acao': 'aprovar', 'ids': [self.a1.pk, self.a2.pk],
        })
        self.assertEqual(resp.status_code, 302)
        for at in (self.a1, self.a2):
            at.refresh_from_db()
            self.assertEqual(at.status, 'AGENDADO')
            # LogAuditoria da transicao so e gravado pelos metodos da FSM
            self.assertTrue(LogAuditoria.objects.filter(
                registro_id=at.pk, acao__contains='PENDENTE -> AGENDADO').exists())

    def test_bulk_rejeitar_ignora_nao_pendente(self):
        self.a2.status = 'CONFIRMADO'
        self.a2.save()
        self.client.post(reverse('aranha:admin_bulk_agendamentos'), {
            'acao': 'rejeitar', 'ids': [self.a1.pk, self.a2.pk],
        })
        self.a1.refresh_from_db()
        self.a2.refresh_from_db()
        self.assertEqual(self.a1.status, 'CANCELADO')
        self.assertEqual(self.a2.status, 'CONFIRMADO')

    def test_redirect_referer_externo_ignorado(self):
        resp = self.client.post(
            reverse('aranha:admin_aprovar_agendamento', args=[self.a1.pk]),
            HTTP_REFERER='https://evil.example.com/x',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('evil.example.com', resp['Location'])

    def test_calendario_filtro_nao_numerico_nao_da_500(self):
        inicio = timezone.now().date().isoformat()
        fim = (timezone.now().date() + timedelta(days=7)).isoformat()
        resp = self.client.get(reverse('aranha:admin_calendar_events'),
                               {'start': inicio, 'end': fim, 'profissional': 'abc'})
        self.assertEqual(resp.status_code, 200)

    def test_calendario_mover_conflito_de_banco_vira_409(self):
        import json
        novo = self.a1.data_hora_inicio + timedelta(days=1)
        with patch.object(Atendimento, 'save', side_effect=IntegrityError('excl_atendimento_sobreposicao')):
            resp = self.client.post(
                reverse('aranha:admin_calendar_mover'),
                data=json.dumps({'id': self.a1.pk, 'start': novo.isoformat(),
                                 'end': (novo + timedelta(minutes=30)).isoformat()}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 409)
        self.assertFalse(resp.json()['sucesso'])

    def test_calendario_mover_fim_antes_do_inicio(self):
        import json
        novo = self.a1.data_hora_inicio + timedelta(days=1)
        resp = self.client.post(
            reverse('aranha:admin_calendar_mover'),
            data=json.dumps({'id': self.a1.pk, 'start': novo.isoformat(), 'end': novo.isoformat()}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)


# ─── Portal do profissional ───────────────────────────────────────────
class PortalProfissionalTests(TestCase):
    def setUp(self):
        self.prof = criar_profissional('Dra. Portal')
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('200.00'))
        self.user = Usuario.objects.create_user(
            email='prof-p5@test.com', password='senha-forte-123', nome='Dra. Portal',
            papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof,
        )
        self.client.force_login(self.user)
        self.cliente = criar_cliente(email='cli-portal@test.com')

    def test_marcar_realizado_gera_comissao_via_fsm(self):
        RegraComissao.objects.create(profissional=self.prof, procedimento=self.proc,
                                     percentual=Decimal('30.00'), ativo=True)
        at = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')
        at.valor_cobrado = Decimal('200.00')
        at.save()
        resp = self.client.post(reverse('aranha:profissional_marcar_realizado', args=[at.pk]))
        self.assertEqual(resp.status_code, 302)
        at.refresh_from_db()
        self.assertEqual(at.status, 'REALIZADO')
        self.assertTrue(MovimentoComissao.objects.filter(atendimento=at).exists())

    def test_marcar_realizado_transicao_invalida(self):
        at = criar_atendimento(self.cliente, self.prof, self.proc, status='PENDENTE')
        resp = self.client.post(reverse('aranha:profissional_marcar_realizado', args=[at.pk]))
        at.refresh_from_db()
        self.assertEqual(at.status, 'PENDENTE')
        self.assertTrue(any('Não é possível' in m for m in _mensagens(resp)))

    def test_next_externo_ignorado(self):
        at = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')
        resp = self.client.post(reverse('aranha:profissional_marcar_realizado', args=[at.pk]),
                                {'next': 'https://evil.example.com/'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('aranha:profissional_agenda'))

    def test_next_local_respeitado(self):
        at = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')
        destino = reverse('aranha:profissional_agenda') + '?data=2026-01-05'
        resp = self.client.post(reverse('aranha:profissional_marcar_realizado', args=[at.pk]),
                                {'next': destino})
        self.assertEqual(resp['Location'], destino)

    def test_aprovar_e_rejeitar_via_service(self):
        a1 = criar_atendimento(self.cliente, self.prof, self.proc, status='PENDENTE')
        base = timezone.now() + timedelta(days=3)
        a2 = criar_atendimento(criar_cliente('Outra'), self.prof, self.proc,
                               data_hora=base.replace(hour=15, minute=0, second=0, microsecond=0),
                               status='PENDENTE')
        self.client.post(reverse('aranha:profissional_aprovar', args=[a1.pk]))
        self.client.post(reverse('aranha:profissional_rejeitar', args=[a2.pk]))
        a1.refresh_from_db()
        a2.refresh_from_db()
        self.assertEqual(a1.status, 'AGENDADO')
        self.assertEqual(a2.status, 'CANCELADO')
        self.assertTrue(LogAuditoria.objects.filter(registro_id=a2.pk, acao__contains='Rejeitado pelo profissional').exists())

    def test_email_de_aprovacao_com_hora_local(self):
        local = timezone.localtime(timezone.now() + timedelta(days=4)).replace(
            hour=10, minute=0, second=0, microsecond=0)
        at = criar_atendimento(self.cliente, self.prof, self.proc, data_hora=local, status='PENDENTE')
        with patch('aranha_estetica.tasks.send_email_async.delay') as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse('aranha:profissional_aprovar', args=[at.pk]))
        self.assertTrue(delay.called)
        dados = delay.call_args[0][2]
        self.assertIn('10:00', dados['data_hora'])

    def test_agenda_tem_botao_sair_e_mensagens(self):
        at = criar_atendimento(self.cliente, self.prof, self.proc, status='AGENDADO')
        resp = self.client.post(reverse('aranha:profissional_marcar_realizado', args=[at.pk]), follow=True)
        self.assertContains(resp, reverse('aranha:usuario_logout'))
        self.assertContains(resp, 'marcado como realizado')


# ─── Termos ───────────────────────────────────────────────────────────
class TermosTests(_AdminTestCase):
    def _publicar(self, **extra):
        data = {'tipo': 'LGPD', 'titulo': 'Política', 'conteudo': 'Texto', 'versao': '2.0'}
        data.update(extra)
        return self.client.post(reverse('aranha:admin_criar_termo'), data)

    def test_publicar_nova_versao_arquiva_anterior(self):
        v1 = VersaoTermo.objects.create(tipo='LGPD', titulo='v1', conteudo='x', versao='1.0',
                                        vigente_desde=timezone.localdate(), ativa=True)
        resp = self._publicar()
        self.assertEqual(resp.status_code, 302)
        v1.refresh_from_db()
        self.assertFalse(v1.ativa)
        v2 = VersaoTermo.objects.get(versao='2.0')
        self.assertTrue(v2.ativa)
        self.assertEqual(VersaoTermo.objects.filter(tipo='LGPD', ativa=True).count(), 1)

    def test_lgpd_ignora_procedimento(self):
        proc = criar_procedimento()
        self._publicar(procedimento_id=str(proc.pk))
        self.assertIsNone(VersaoTermo.objects.get(versao='2.0').procedimento)

    def test_tipo_invalido(self):
        self._publicar(tipo='XPTO')
        self.assertFalse(VersaoTermo.objects.exists())

    def test_termo_por_procedimento_nao_arquiva_lgpd(self):
        proc = criar_procedimento()
        lgpd = VersaoTermo.objects.create(tipo='LGPD', titulo='v1', conteudo='x', versao='1.0',
                                          vigente_desde=timezone.localdate(), ativa=True)
        self._publicar(tipo='PROCEDIMENTO', procedimento_id=str(proc.pk))
        lgpd.refresh_from_db()
        self.assertTrue(lgpd.ativa)

    def test_compliance_versao_nao_numerica(self):
        resp = self.client.get(reverse('aranha:admin_termos_compliance'), {'versao': 'abc'})
        self.assertEqual(resp.status_code, 200)


# ─── Cliente ──────────────────────────────────────────────────────────
class ClienteEdicaoTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.c1 = criar_cliente('Cliente Um', email='um@test.com')
        self.c1.consent_email_marketing = True
        self.c1.save()
        self.c2 = criar_cliente('Cliente Dois', email='dois@test.com')
        self.url = reverse('aranha:admin_cliente_detalhe', args=[self.c1.pk])

    def _post(self, **extra):
        data = {'nome': 'Cliente Um', 'telefone': self.c1.telefone, 'email': 'um@test.com', 'ativo': '1'}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_email_duplicado_nao_da_500(self):
        resp = self._post(email='DOIS@test.com')
        self.assertEqual(resp.status_code, 200)
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.email, 'um@test.com')

    def test_telefone_duplicado_nao_da_500(self):
        resp = self._post(telefone=self.c2.telefone)
        self.assertEqual(resp.status_code, 200)
        self.c1.refresh_from_db()
        self.assertNotEqual(self.c1.telefone, self.c2.telefone)

    def test_cpf_invalido_nao_salva(self):
        resp = self._post(cpf='111.111.111-11')
        self.assertEqual(resp.status_code, 200)
        self.c1.refresh_from_db()
        self.assertIsNone(self.c1.cpf)

    def test_telefone_curto_rejeitado(self):
        resp = self._post(telefone='99999-888')
        self.assertEqual(resp.status_code, 200)

    def test_edicao_valida_preserva_consents(self):
        resp = self._post(nome='Cliente Um Editado', telefone='+55 (17) 98888-7777')
        self.assertEqual(resp.status_code, 302)
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.nome, 'Cliente Um Editado')
        self.assertEqual(self.c1.telefone, '17988887777')
        self.assertTrue(self.c1.consent_email_marketing)


# ─── Procedimentos / preco com vigencia ───────────────────────────────
class PrecoVigenciaTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.proc = criar_procedimento(preco=None)
        hoje = timezone.localdate()
        Preco.objects.create(procedimento=self.proc, valor=Decimal('150.00'),
                             vigente_desde=hoje - timedelta(days=60))
        Preco.objects.create(procedimento=self.proc, valor=Decimal('180.00'),
                             vigente_desde=hoje - timedelta(days=10))

    def test_listagem_mostra_vigente_sem_virgula_no_input(self):
        resp = self.client.get(reverse('aranha:admin_procedimentos'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="180.00"')

    def test_editar_preco_cria_nova_vigencia(self):
        resp = self.client.post(reverse('aranha:admin_editar_procedimento', args=[self.proc.pk]), {
            'nome': self.proc.nome, 'duracao_minutos': '30', 'categoria': self.proc.categoria,
            'ativo': '1', 'preco': '199,90',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Preco.objects.filter(procedimento=self.proc).count(), 3)
        atual = Preco.objects.filter(procedimento=self.proc).order_by('-vigente_desde').first()
        self.assertEqual(atual.valor, Decimal('199.90'))
        self.assertEqual(atual.vigente_desde, timezone.localdate())

    def test_editar_sem_mudar_preco_nao_cria_vigencia(self):
        self.client.post(reverse('aranha:admin_editar_procedimento', args=[self.proc.pk]), {
            'nome': 'Novo nome', 'duracao_minutos': '30', 'categoria': self.proc.categoria,
            'ativo': '1', 'preco': '180.00',
        })
        self.assertEqual(Preco.objects.filter(procedimento=self.proc).count(), 2)
        self.proc.refresh_from_db()
        self.assertEqual(self.proc.nome, 'Novo nome')


# ─── Bloqueios / excecoes ─────────────────────────────────────────────
class BloqueioExcecaoTests(_AdminTestCase):
    def test_excluir_bloqueio_global(self):
        agora = timezone.now()
        b = BloqueioAgenda.objects.create(profissional=None, data_hora_inicio=agora,
                                          data_hora_fim=agora + timedelta(hours=1))
        resp = self.client.post(reverse('aranha:admin_excluir_bloqueio', args=[b.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(BloqueioAgenda.objects.filter(pk=b.pk).exists())

    def test_excecao_horario_fim_antes_do_inicio(self):
        prof = criar_profissional()
        data = (timezone.localdate() + timedelta(days=5)).isoformat()
        resp = self.client.post(reverse('aranha:admin_excecao_criar', args=[prof.pk]), {
            'data': data, 'tipo': 'HORARIO_DIFERENTE', 'hora_inicio': '14:00', 'hora_fim': '09:00',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ExcecaoDisponibilidade.objects.exists())

    def test_excecao_hora_malformada(self):
        prof = criar_profissional()
        data = (timezone.localdate() + timedelta(days=5)).isoformat()
        resp = self.client.post(reverse('aranha:admin_excecao_criar', args=[prof.pk]), {
            'data': data, 'tipo': 'HORARIO_DIFERENTE', 'hora_inicio': '25:99', 'hora_fim': '26:00',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ExcecaoDisponibilidade.objects.exists())


# ─── Pacotes ──────────────────────────────────────────────────────────
class PacotesTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.proc = criar_procedimento()
        self.p1 = criar_pacote('Pacote A', preco=Decimal('1350.00'), procedimento=self.proc)
        self.p2 = criar_pacote('Pacote B', preco=Decimal('600.00'), procedimento=self.proc)
        for i in range(3):
            criar_cliente(f'Cliente {i}')

    def test_render_unico_select_de_cliente_e_valor_sem_virgula(self):
        resp = self.client.get(reverse('aranha:admin_pacotes'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertEqual(html.count('name="cliente_id"'), 1)
        self.assertIn('value="1350.00"', html)
        self.assertIn(reverse('aranha:admin_editar_pacote', args=[self.p1.pk]), html)

    def test_editar_desativa_pacote(self):
        resp = self.client.post(reverse('aranha:admin_editar_pacote', args=[self.p1.pk]), {
            'nome': 'Pacote A', 'preco_total': '1.350,00'.replace('.', ''), 'validade_meses': '6',
            'procedimento_ids': [str(self.proc.pk)], 'quantidades': ['5'],
        })
        self.assertEqual(resp.status_code, 302)
        self.p1.refresh_from_db()
        self.assertFalse(self.p1.ativo)
        self.assertEqual(self.p1.preco_total, Decimal('1350.00'))
        self.assertEqual(self.p1.itens.get().quantidade_sessoes, 5)

    def test_editar_item_repetido_nao_apaga(self):
        self.client.post(reverse('aranha:admin_editar_pacote', args=[self.p1.pk]), {
            'nome': 'Pacote A', 'preco_total': '1350', 'validade_meses': '6', 'ativo': '1',
            'procedimento_ids': [str(self.proc.pk), str(self.proc.pk)], 'quantidades': ['1', '2'],
        })
        self.assertEqual(self.p1.itens.count(), 1)

    def test_vender_pacote_inativo_bloqueado(self):
        self.p2.ativo = False
        self.p2.save()
        cli = Cliente.objects.first()
        self.client.post(reverse('aranha:admin_vender_pacote'), {
            'pacote_id': self.p2.pk, 'cliente_id': cli.pk, 'valor_pago': '600',
        })
        self.assertFalse(CompraPacote.objects.exists())

    def test_vender_expira_pela_data_local(self):
        cli = Cliente.objects.first()
        self.client.post(reverse('aranha:admin_vender_pacote'), {
            'pacote_id': self.p1.pk, 'cliente_id': cli.pk, 'valor_pago': '1350,00',
        })
        from dateutil.relativedelta import relativedelta
        compra = CompraPacote.objects.get()
        self.assertEqual(compra.valor_pago, Decimal('1350.00'))
        self.assertEqual(compra.data_expiracao,
                         timezone.localdate() + relativedelta(months=self.p1.validade_meses))


# ─── Promocoes ────────────────────────────────────────────────────────
class PromocoesTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.proc = criar_procedimento()
        hoje = timezone.localdate()
        self.promo = Promocao.objects.create(
            nome='Primavera', desconto_percentual=Decimal('15.00'), procedimento=self.proc,
            data_inicio=hoje, data_fim=hoje + timedelta(days=30),
        )
        self.url = reverse('aranha:admin_disparar_promocao', args=[self.promo.pk])

    def test_lista_com_desconto_sem_virgula_e_botao_disparo(self):
        resp = self.client.get(reverse('aranha:admin_promocoes'))
        self.assertContains(resp, 'value="15.00"')
        self.assertContains(resp, self.url)

    def test_editar_desconto_decimal(self):
        hoje = timezone.localdate()
        self.client.post(reverse('aranha:admin_editar_promocao', args=[self.promo.pk]), {
            'nome': 'Primavera', 'procedimento': self.proc.pk, 'desconto': '12,5',
            'data_inicio': hoje.isoformat(), 'data_fim': (hoje + timedelta(days=5)).isoformat(),
            'ativa': '1',
        })
        self.promo.refresh_from_db()
        self.assertEqual(self.promo.desconto_percentual, Decimal('12.50'))

    @patch('aranha_estetica.tasks.job_promocao_mensal.delay')
    def test_validade_vazia_nao_da_500(self, mock_delay):
        resp = self.client.post(self.url, {'validade_dias': ''})
        self.assertEqual(resp.status_code, 302)
        mock_delay.assert_called_once()

    @patch('aranha_estetica.tasks.job_promocao_mensal.delay')
    def test_validade_invalida(self, mock_delay):
        resp = self.client.post(self.url, {'validade_dias': 'abc'})
        self.assertEqual(resp.status_code, 302)
        mock_delay.assert_not_called()

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend', DEBUG=False)
    @patch('aranha_estetica.tasks.job_promocao_mensal.delay')
    def test_email_nao_configurado_nao_dispara(self, mock_delay):
        resp = self.client.post(self.url, {'validade_dias': '30'})
        mock_delay.assert_not_called()
        self.assertTrue(any('não está configurado' in m for m in _mensagens(resp)))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_lista_grande_sem_worker_vai_em_lotes_sem_reenvio(self):
        from aranha_estetica.views import admin_promotions
        for i in range(admin_promotions.LOTE_PROMOCAO + 5):
            criar_cliente(f'Cli {i}', email=f'cli{i}@test.com',
                          consent_email_marketing=True, aceita_comunicacao=True)
        self.client.post(self.url, {'validade_dias': '30'})
        self.assertEqual(len(mail.outbox), admin_promotions.LOTE_PROMOCAO)
        self.assertTrue(Configuracao.objects.filter(chave__startswith='promocao_').exists())
        self.client.post(self.url, {'validade_dias': '30'})
        self.assertEqual(len(mail.outbox), admin_promotions.LOTE_PROMOCAO + 5)
        destinatarios = [m.to[0] for m in mail.outbox]
        self.assertEqual(len(destinatarios), len(set(destinatarios)))
        self.assertFalse(Configuracao.objects.filter(chave__startswith='promocao_').exists())


# ─── Lista de espera ──────────────────────────────────────────────────
class ListaEsperaPainelTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.proc = criar_procedimento()

    def _item(self, **cliente_kwargs):
        cli = criar_cliente('Espera', **cliente_kwargs)
        return ListaEspera.objects.create(cliente=cli, procedimento=self.proc,
                                          data_desejada=timezone.localdate() + timedelta(days=3))

    def test_lista_mostra_contato(self):
        item = self._item(email='espera@test.com')
        resp = self.client.get(reverse('aranha:admin_lista_espera'))
        self.assertContains(resp, item.cliente.telefone)
        self.assertContains(resp, 'espera@test.com')
        self.assertContains(resp, f'https://wa.me/55{item.cliente.telefone}')

    def test_avisar_com_email_envia(self):
        item = self._item(email='espera@test.com')
        resp = self.client.post(reverse('aranha:admin_notificar_espera', args=[item.pk]))
        self.assertEqual(resp.status_code, 302)
        item.refresh_from_db()
        self.assertTrue(item.notificado)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['espera@test.com'])

    def test_avisar_sem_email_so_marca(self):
        item = self._item()
        resp = self.client.post(reverse('aranha:admin_notificar_espera', args=[item.pk]))
        item.refresh_from_db()
        self.assertTrue(item.notificado)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(any('Nenhum e-mail foi enviado' in m for m in _mensagens(resp)))


# ─── Paginacao / Google Calendar ──────────────────────────────────────
class PaginacaoGcalTests(_AdminTestCase):
    def test_agendamentos_pagina_2_preserva_filtro(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cli = criar_cliente()
        base = timezone.now() + timedelta(days=1)
        for i in range(55):
            inicio = base + timedelta(hours=i)
            Atendimento.objects.create(cliente=cli, profissional=prof, procedimento=proc,
                                       data_hora_inicio=inicio,
                                       data_hora_fim=inicio + timedelta(minutes=30),
                                       status='AGENDADO')
        resp = self.client.get(reverse('aranha:painel_agendamentos'), {'status': 'agendado'})
        self.assertContains(resp, 'status=agendado')
        self.assertContains(resp, 'page=2')

    def test_gcal_escondido_sem_configuracao(self):
        criar_profissional()
        resp = self.client.get(reverse('aranha:painel_profissionais'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Google Calendar')

    def test_rotas_gcal_404_sem_configuracao(self):
        prof = criar_profissional()
        self.assertEqual(self.client.get(reverse('aranha:gcal_connect', args=[prof.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('aranha:gcal_pull', args=[prof.pk])).status_code, 404)

    def test_gcal_pull_exige_post(self):
        prof = criar_profissional()
        with patch('aranha_estetica.services.gcal.gcal_disponivel', return_value=True):
            self.assertEqual(self.client.get(reverse('aranha:gcal_pull', args=[prof.pk])).status_code, 405)


# ─── NPS web: opt-in de publicacao (contrato 4) ───────────────────────
class NpsOptInTests(TestCase):
    def _notif(self):
        from aranha_estetica.models import Notificacao
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        at = criar_atendimento(criar_cliente(), prof, proc, status='REALIZADO')
        return Notificacao.objects.create(atendimento=at, tipo='NPS', canal='EMAIL', token='tok-nps-p5')

    def test_optin_marcado_grava_autorizacao(self):
        from aranha_estetica.models import AvaliacaoNPS
        notif = self._notif()
        self.client.post(reverse('aranha:nps_web', args=[notif.token]),
                         {'nota': '10', 'comentario': 'Amei', 'autoriza_publicacao': '1'})
        av = AvaliacaoNPS.objects.get(atendimento=notif.atendimento)
        self.assertTrue(av.autoriza_publicacao)
        self.assertFalse(av.aprovado_publicacao)

    def test_sem_optin_nao_autoriza(self):
        from aranha_estetica.models import AvaliacaoNPS
        notif = self._notif()
        self.client.post(reverse('aranha:nps_web', args=[notif.token]), {'nota': '9'})
        self.assertFalse(AvaliacaoNPS.objects.get(atendimento=notif.atendimento).autoriza_publicacao)


# ═══════════════════════════════════════════════════════════════════════
#   Rodada 2 (P5): alerta de saude visivel, termos, agendamento interno,
#   pacotes vendidos, promocao
# ═══════════════════════════════════════════════════════════════════════

def _local(dias=1, hora=10):
    """Datetime aware no fuso local, `dias` a frente, na hora cheia."""
    return timezone.localtime(timezone.now() + timedelta(days=dias)).replace(
        hour=hora, minute=0, second=0, microsecond=0)


def _lgpd():
    return VersaoTermo.lgpd_vigente() or VersaoTermo.objects.create(
        tipo='LGPD', titulo='Privacidade', conteudo='Texto LGPD', versao='1.0',
        vigente_desde=timezone.localdate(), ativa=True)


def _termo_proc(proc=None, **kwargs):
    dados = {'titulo': 'Termo do procedimento', 'conteudo': 'Riscos e cuidados', 'versao': '1.0'}
    dados.update(kwargs)
    return VersaoTermo.objects.create(
        tipo='PROCEDIMENTO', procedimento=proc, vigente_desde=timezone.localdate(), ativa=True, **dados)


class AlertaSaudeVisivelTests(TestCase):
    """Alergia aparece como TEXTO (nao title) no portal, painel, anotar e ficha."""

    ALERTA = '<strong>Alergias:</strong> Lidocaina'

    def setUp(self):
        from aranha_estetica.models import Prontuario
        self.prof = criar_profissional('Dra. Alerta')
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente('Dora Alergica')
        Prontuario.objects.create(cliente=self.cli, alergias='Lidocaina')
        self.user = Usuario.objects.create_user(
            email='prof-alerta@test.com', password='senha-forte-123', nome='Dra. Alerta',
            papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof,
        )
        self.agendado = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(1, 10))
        self.pendente = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(1, 14),
                                          status='PENDENTE')

    def test_portal_dia_e_pendentes_mostram_texto(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('aranha:profissional_agenda'),
                               {'data': _local(1).date().isoformat()})
        html = resp.content.decode()
        # 2 cards do dia + 1 na lista "Aguardando sua aprovacao"
        self.assertEqual(html.count(self.ALERTA), 3)
        self.assertNotIn('title="Alergias', html)
        self.assertIn('Alerta de saúde: Alergias: Lidocaina', html)  # visao semana (sr-only)
        self.assertIn(reverse('aranha:prontuario_detalhe', args=[self.cli.pk]), html)

    def test_anotar_mostra_alerta(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('aranha:profissional_anotar', args=[self.agendado.pk]))
        self.assertContains(resp, self.ALERTA)

    def test_painel_agendamentos_e_ficha(self):
        admin = Usuario.objects.create_superuser(email='adm-alerta@test.com', password='x-senha-123', nome='Adm')
        self.client.force_login(admin)
        resp = self.client.get(reverse('aranha:painel_agendamentos'))
        self.assertContains(resp, self.ALERTA)
        self.assertNotContains(resp, 'title="Alergias')
        resp = self.client.get(reverse('aranha:admin_cliente_detalhe', args=[self.cli.pk]))
        self.assertContains(resp, self.ALERTA)

    def test_ficha_de_anamnese_respondida_entra_no_alerta(self):
        from aranha_estetica.models import FormularioAnamnese, RespostaAnamnese
        from aranha_estetica.services.alertas import alertas_por_cliente
        outra = criar_cliente('Gestante')
        form = FormularioAnamnese.objects.create(
            nome='Ficha', tipo='ANAMNESE',
            schema_json=[{'key': 'gestante', 'label': 'Está gestante?', 'type': 'text'}],
        )
        RespostaAnamnese.objects.create(formulario=form, cliente=outra, respostas_json={'gestante': 'Sim'},
                                        respondida_em=timezone.now())
        mapa = alertas_por_cliente([outra, self.cli, criar_cliente('Sem nada')])
        self.assertEqual(set(mapa), {outra.pk, self.cli.pk})
        self.assertEqual(mapa[outra.pk][0]['origem'], 'anamnese')


class TermoAssinaturaTests(TestCase):
    def setUp(self):
        from aranha_estetica.models import Notificacao
        self.Notificacao = Notificacao
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente('Tina Termo')
        self.at = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(30, 10))
        self.lgpd = _lgpd()
        self.termo = _termo_proc(self.proc)
        self.notif = Notificacao.objects.create(atendimento=self.at, tipo='TERMO', canal='EMAIL',
                                                token='tok-termo-p5')
        self.url = reverse('aranha:termo_assinatura', args=[self.notif.token])

    def _aceites(self):
        from aranha_estetica.models import AceiteTermo
        return AceiteTermo.objects.filter(cliente=self.cli)

    def test_token_de_nps_nao_abre_termo(self):
        nps = self.Notificacao.objects.create(atendimento=self.at, tipo='NPS', canal='EMAIL', token='tok-nps-x')
        self.assertEqual(self.client.get(reverse('aranha:termo_assinatura', args=[nps.token])).status_code, 404)

    def test_post_parcial_400_sem_gravar(self):
        resp = self.client.post(self.url, {f'aceite_{self.lgpd.pk}': '1'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('erro', resp.context)
        self.assertFalse(self._aceites().exists())

    def test_post_completo_grava_prova_e_auditoria(self):
        resp = self.client.post(
            self.url, {f'aceite_{self.lgpd.pk}': '1', f'aceite_{self.termo.pk}': '1'},
            HTTP_USER_AGENT='Mozilla/5.0 (P5)',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._aceites().count(), 2)
        aceite = self._aceites().get(versao_termo=self.termo)
        self.assertEqual(aceite.user_agent, 'Mozilla/5.0 (P5)')
        self.assertEqual(aceite.atendimento_id, self.at.pk)
        self.assertEqual(aceite.conteudo_sha256, self.termo.sha256_conteudo)
        self.assertTrue(LogAuditoria.objects.filter(acao='Cliente aceitou termos', registro_id=self.cli.pk).exists())

    def test_link_antigo_vale_ate_o_atendimento(self):
        self.Notificacao.objects.filter(pk=self.notif.pk).update(criado_em=timezone.now() - timedelta(days=8))
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_cancelado_realizado_ou_encerrado_410(self):
        for status in ('CANCELADO', 'REALIZADO'):
            Atendimento.objects.filter(pk=self.at.pk).update(status=status)
            resp = self.client.post(self.url, {f'aceite_{self.lgpd.pk}': '1', f'aceite_{self.termo.pk}': '1'})
            self.assertEqual(resp.status_code, 410)
        passado = timezone.now() - timedelta(days=1)
        Atendimento.objects.filter(pk=self.at.pk).update(
            status='AGENDADO', data_hora_inicio=passado - timedelta(minutes=30), data_hora_fim=passado)
        self.assertEqual(self.client.get(self.url).status_code, 410)
        self.assertFalse(self._aceites().exists())

    def test_termo_procedimento_geral_aparece(self):
        geral = _termo_proc(None, titulo='Termo geral de estética')
        resp = self.client.get(self.url)
        self.assertContains(resp, 'Termo geral de estética')
        self.assertIn(geral, resp.context['termos_pendentes'])


class TermoLinkEPendenciaTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.cli = criar_cliente('Rita Balcão')
        self.at = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(5, 11))
        self.termo = _termo_proc(self.proc)

    def test_gerar_link_cria_notificacao_termo_e_reusa(self):
        from aranha_estetica.models import Notificacao
        url = reverse('aranha:admin_gerar_link_termo', args=[self.at.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200)
        notif = Notificacao.objects.get(atendimento=self.at)
        self.assertEqual(notif.tipo, 'TERMO')
        self.assertContains(resp, reverse('aranha:termo_assinatura', args=[notif.token]))
        self.client.post(url)
        self.assertEqual(Notificacao.objects.filter(atendimento=self.at).count(), 1)
        # e o link gerado abre a pagina publica do termo
        self.client.logout()
        self.assertEqual(self.client.get(reverse('aranha:termo_assinatura', args=[notif.token])).status_code, 200)

    def test_gerar_link_de_cancelado_recusa(self):
        from aranha_estetica.models import Notificacao
        Atendimento.objects.filter(pk=self.at.pk).update(status='CANCELADO')
        resp = self.client.post(reverse('aranha:admin_gerar_link_termo', args=[self.at.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Notificacao.objects.exists())

    def test_painel_e_ficha_mostram_termo_pendente(self):
        resp = self.client.get(reverse('aranha:painel_agendamentos'))
        self.assertContains(resp, 'Termo pendente')
        self.assertContains(resp, reverse('aranha:admin_gerar_link_termo', args=[self.at.pk]))
        resp = self.client.get(reverse('aranha:admin_cliente_detalhe', args=[self.cli.pk]))
        self.assertContains(resp, 'Termo pendente')

    def test_compliance_oferece_link_do_proximo_atendimento(self):
        resp = self.client.get(reverse('aranha:admin_termos_compliance'), {'versao': self.termo.pk})
        self.assertContains(resp, reverse('aranha:admin_gerar_link_termo', args=[self.at.pk]))

    def test_portal_realizado_sem_termo_exige_confirmacao_e_audita(self):
        user = Usuario.objects.create_user(
            email='prof-termo@test.com', password='senha-forte-123', nome='Prof',
            papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof,
        )
        self.client.force_login(user)
        url = reverse('aranha:profissional_marcar_realizado', args=[self.at.pk])
        self.client.post(url)
        self.at.refresh_from_db()
        self.assertEqual(self.at.status, 'AGENDADO')
        self.client.post(url, {'sem_termo': '1'})
        self.at.refresh_from_db()
        self.assertEqual(self.at.status, 'REALIZADO')
        self.assertTrue(LogAuditoria.objects.filter(acao='Realizado sem termo aceito', registro_id=self.at.pk).exists())


class TermosCriacaoComplianceTests(_AdminTestCase):
    def test_lgpd_com_procedimento_vira_global_e_arquiva_legado(self):
        proc = criar_procedimento()
        legado = VersaoTermo.objects.create(tipo='LGPD', procedimento=proc, titulo='LGPD presa', conteudo='x',
                                            versao='0.9', vigente_desde=timezone.localdate(), ativa=True)
        self.client.post(reverse('aranha:admin_criar_termo'), {
            'tipo': 'LGPD', 'titulo': 'Política v2', 'conteudo': 'Texto', 'versao': '2.0',
            'procedimento_id': str(proc.pk),
        })
        ativas = VersaoTermo.objects.filter(tipo='LGPD', ativa=True)
        self.assertEqual(ativas.count(), 1)
        self.assertIsNone(ativas.get().procedimento)
        legado.refresh_from_db()
        self.assertFalse(legado.ativa)

    def test_compliance_ignora_aceite_de_inativo(self):
        from aranha_estetica.models import AceiteTermo
        lgpd = _lgpd()
        for i in range(3):
            inativo = criar_cliente(f'Inativo {i}')
            AceiteTermo.objects.create(cliente=inativo, versao_termo=lgpd)
            Cliente.objects.filter(pk=inativo.pk).update(ativo=False)
        criar_cliente('Ativa sem aceite')
        resp = self.client.get(reverse('aranha:admin_termos_compliance'))
        linha = next(r for r in resp.context['resumo_versoes'] if r['versao'].pk == lgpd.pk)
        self.assertEqual((linha['relevantes'], linha['assinados'], linha['pendentes']), (1, 0, 1))
        self.assertEqual(linha['pct'], 0)

    def test_compliance_termo_geral_conta_quem_tem_atendimento(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        geral = _termo_proc(None)
        criar_atendimento(criar_cliente('Com agenda'), prof, proc)
        criar_atendimento(criar_cliente('Cancelou'), prof, proc, data_hora=_local(3, 15), status='CANCELADO')
        resp = self.client.get(reverse('aranha:admin_termos_compliance'))
        linha = next(r for r in resp.context['resumo_versoes'] if r['versao'].pk == geral.pk)
        self.assertEqual(linha['relevantes'], 1)


class AgendamentoInternoTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.prof = criar_profissional('Dra. Recepção')
        self.proc = criar_procedimento(profissional=self.prof, preco=Decimal('150.00'))
        self.cli = criar_cliente('Cliente Balcão')
        self.dia = _local(3).date()
        self.url = reverse('aranha:admin_agendamento_novo')

    def _post(self, **extra):
        data = {'acao': 'agendar', 'cliente_id': str(self.cli.pk), 'procedimento_id': str(self.proc.pk),
                'profissional_id': str(self.prof.pk), 'data': self.dia.isoformat(), 'hora': '10:00'}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_link_novo_agendamento_do_painel(self):
        resp = self.client.get(reverse('aranha:painel_agendamentos'))
        self.assertContains(resp, self.url)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_cria_agendado_sem_otp_com_promocao_e_auditoria(self):
        Promocao.objects.create(nome='Primavera', desconto_percentual=Decimal('20.00'), procedimento=self.proc,
                                data_inicio=self.dia, data_fim=self.dia)
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        at = Atendimento.objects.get(cliente=self.cli)
        self.assertEqual(at.status, 'AGENDADO')
        self.assertEqual(timezone.localtime(at.data_hora_inicio).strftime('%H:%M'), '10:00')
        self.assertEqual(at.valor_cobrado, Decimal('120.00'))
        self.assertEqual(at.valor_original, Decimal('150.00'))
        self.assertIsNotNone(at.promocao_id)
        log = LogAuditoria.objects.get(tabela='atendimento', registro_id=at.pk, acao__startswith='Agendou pelo painel')
        self.assertIsNotNone(log.ip_origem)

    def test_cliente_nova_com_telefone_validado(self):
        resp = self._post(cliente_id='', novo_nome='Nova Cliente', novo_telefone='(17) 98877-6655')
        self.assertEqual(resp.status_code, 302)
        nova = Cliente.objects.get(telefone='17988776655')
        self.assertEqual(Atendimento.objects.get(cliente=nova).status, 'AGENDADO')

    def test_telefone_invalido_ou_ja_cadastrado_recusa(self):
        self._post(cliente_id='', novo_nome='X', novo_telefone='1234')
        self._post(cliente_id='', novo_nome='Y', novo_telefone=self.cli.telefone)
        self.assertFalse(Atendimento.objects.exists())

    def test_slot_ocupado_recusado(self):
        from datetime import datetime as _dt
        criar_atendimento(criar_cliente('Ocupa'), self.prof, self.proc,
                          data_hora=timezone.make_aware(_dt.combine(self.dia, time(10, 0))))
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Atendimento.objects.filter(cliente=self.cli).exists())
        self.assertTrue(any('não está livre' in m for m in _mensagens(resp)))

    def test_valor_combinado_fica_registrado(self):
        self._post(valor='99,90')
        at = Atendimento.objects.get(cliente=self.cli)
        self.assertEqual(at.valor_cobrado, Decimal('99.90'))
        self.assertEqual(at.valor_original, Decimal('150.00'))
        self.assertIn('recepção', at.descricao_preco)

    def test_ver_horarios_lista_slots_livres(self):
        resp = self.client.post(self.url, {'acao': 'horarios', 'procedimento_id': self.proc.pk,
                                           'profissional_id': self.prof.pk, 'data': self.dia.isoformat()})
        self.assertContains(resp, 'value="10:00"')
        self.assertFalse(Atendimento.objects.exists())


class PacoteVendidoTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        from .factories import criar_compra_pacote
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof)
        self.pacote = criar_pacote('Drenagem 10x', preco=Decimal('1500.00'), procedimento=self.proc, sessoes=10)
        self.cli = criar_cliente('Paula Pacote')
        self.compra = criar_compra_pacote(self.cli, self.pacote)

    def test_editar_itens_de_pacote_vendido_bloqueado(self):
        resp = self.client.post(reverse('aranha:admin_editar_pacote', args=[self.pacote.pk]), {
            'nome': 'Drenagem 10x', 'preco_total': '1500', 'validade_meses': '12', 'ativo': '1',
            'procedimento_ids': [str(self.proc.pk)], 'quantidades': ['2'],
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.pacote.itens.get().quantidade_sessoes, 10)
        self.assertTrue(any('já foi vendido' in m for m in _mensagens(resp)))
        self.assertEqual(self.compra.saldo_por_procedimento()[0]['restantes'], 10)

    def test_pacote_vendido_aceita_preco_sem_itens_no_form(self):
        self.client.post(reverse('aranha:admin_editar_pacote', args=[self.pacote.pk]), {
            'nome': 'Drenagem 10x', 'preco_total': '1600', 'validade_meses': '12', 'ativo': '1',
        })
        self.pacote.refresh_from_db()
        self.assertEqual(self.pacote.preco_total, Decimal('1600.00'))
        self.assertEqual(self.pacote.itens.get().quantidade_sessoes, 10)

    def test_ficha_mostra_saldo_validade_e_sessao_de_pacote(self):
        from aranha_estetica.models import ConsumoSessao
        at = criar_atendimento(self.cli, self.prof, self.proc, status='REALIZADO')
        Atendimento.objects.filter(pk=at.pk).update(valor_cobrado=Decimal('150.00'))
        ConsumoSessao.objects.get_or_create(compra_pacote=self.compra, atendimento=at)
        resp = self.client.get(reverse('aranha:admin_cliente_detalhe', args=[self.cli.pk]))
        self.assertContains(resp, '1/10 usadas')
        self.assertContains(resp, self.compra.data_expiracao.strftime('%d/%m/%Y'))
        self.assertContains(resp, 'Sessão de pacote')
        resp = self.client.get(reverse('aranha:painel_agendamentos'))
        self.assertContains(resp, '>Pacote</span>')
        self.assertNotContains(resp, 'R$ 150,00')

    def test_cancelar_compra_exige_observacao_e_audita(self):
        url = reverse('aranha:admin_cancelar_compra_pacote', args=[self.compra.pk])
        self.client.post(url, {'observacao': ''})
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.status, 'ATIVO')
        self.client.post(url, {'observacao': 'Reembolso integral via PIX', 'valor_reembolsado': '1500,00'})
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.status, 'CANCELADO')
        self.assertTrue(LogAuditoria.objects.filter(tabela='compra_pacote', registro_id=self.compra.pk,
                                                    acao__startswith='Cancelou pacote').exists())


class PromocaoDisparoRodada2Tests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.proc = criar_procedimento()
        hoje = timezone.localdate()
        self.promo = Promocao.objects.create(
            nome='Setembro Glow', preco_promocional=Decimal('99.00'), procedimento=self.proc,
            data_inicio=hoje, data_fim=hoje + timedelta(days=10),
        )
        self.url = reverse('aranha:admin_disparar_promocao', args=[self.promo.pk])

    @patch('aranha_estetica.tasks.job_promocao_mensal.delay')
    def test_preco_fixo_sem_zero_off_e_validade_ate_fim_da_promocao(self, mock_delay):
        self.client.post(self.url, {'validade_dias': '60'})
        mock_delay.assert_called_once()
        assunto = mock_delay.call_args[0][0]
        self.assertNotIn('0% OFF', assunto)
        self.assertIn('99,00', assunto)
        self.assertEqual(mock_delay.call_args[1]['validade_dias'], 10)
        self.assertNotIn('cupom', mock_delay.call_args[1])

    @patch('aranha_estetica.tasks.job_promocao_mensal.delay')
    def test_promocao_expirada_nao_dispara(self, mock_delay):
        Promocao.objects.filter(pk=self.promo.pk).update(
            data_inicio=timezone.localdate() - timedelta(days=20),
            data_fim=timezone.localdate() - timedelta(days=1))
        self.client.post(self.url, {'validade_dias': '30'})
        mock_delay.assert_not_called()

    def test_lista_sem_campo_cupom_e_mostra_preco_fixo(self):
        resp = self.client.get(reverse('aranha:admin_promocoes'))
        self.assertNotContains(resp, 'name="cupom"')
        self.assertContains(resp, 'R$ 99,00')


class NpsNotaInvalidaTests(TestCase):
    def test_nota_fora_da_escala_mostra_erro(self):
        from aranha_estetica.models import AvaliacaoNPS, Notificacao
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        at = criar_atendimento(criar_cliente(), prof, proc, status='REALIZADO')
        notif = Notificacao.objects.create(atendimento=at, tipo='NPS', canal='EMAIL', token='tok-nps-erro')
        resp = self.client.post(reverse('aranha:nps_web', args=[notif.token]), {'nota': '11'})
        self.assertContains(resp, 'Escolha uma nota de 0 a 10.')
        self.assertFalse(AvaliacaoNPS.objects.exists())


class ValorAtendimentoTests(_AdminTestCase):
    def setUp(self):
        super().setUp()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=None)  # "a consultar"
        self.cli = criar_cliente('Valor Depois')

    def test_realizado_sem_valor_gera_comissao_ao_registrar(self):
        RegraComissao.objects.create(profissional=self.prof, procedimento=self.proc,
                                     valor=Decimal('80.00'), ativo=True)
        at = criar_atendimento(self.cli, self.prof, self.proc, status='REALIZADO')
        self.assertFalse(MovimentoComissao.objects.filter(atendimento=at).exists())
        self.client.post(reverse('aranha:admin_atendimento_valor', args=[at.pk]), {'valor': '150,00'})
        at.refresh_from_db()
        self.assertEqual(at.valor_cobrado, Decimal('150.00'))
        self.assertEqual(MovimentoComissao.objects.get(atendimento=at).valor, Decimal('80.00'))
        self.assertTrue(LogAuditoria.objects.filter(acao='Registrou valor cobrado', registro_id=at.pk).exists())

    def test_com_comissao_lancada_ou_cancelado_recusa(self):
        at = criar_atendimento(self.cli, self.prof, self.proc, status='REALIZADO')
        MovimentoComissao.objects.create(profissional=self.prof, atendimento=at, valor=Decimal('10.00'))
        self.client.post(reverse('aranha:admin_atendimento_valor', args=[at.pk]), {'valor': '150'})
        at.refresh_from_db()
        self.assertIsNone(at.valor_cobrado)
        cancelado = criar_atendimento(self.cli, self.prof, self.proc, data_hora=_local(4, 9), status='CANCELADO')
        self.client.post(reverse('aranha:admin_atendimento_valor', args=[cancelado.pk]), {'valor': '150'})
        cancelado.refresh_from_db()
        self.assertIsNone(cancelado.valor_cobrado)

    def test_agendado_registra_desconto_guardando_original(self):
        at = criar_atendimento(self.cli, self.prof, self.proc)
        Atendimento.objects.filter(pk=at.pk).update(valor_cobrado=Decimal('200.00'))
        self.client.post(reverse('aranha:admin_atendimento_valor', args=[at.pk]), {'valor': '180'})
        at.refresh_from_db()
        self.assertEqual((at.valor_cobrado, at.valor_original), (Decimal('180.00'), Decimal('200.00')))


class BloqueioGlobalPainelTests(_AdminTestCase):
    def test_todos_os_profissionais_cria_bloqueio_global(self):
        inicio = _local(6, 9)
        resp = self.client.post(reverse('aranha:admin_criar_bloqueio'), {
            'profissional_id': 'todos', 'motivo': 'Recesso',
            'data_hora_inicio': inicio.strftime('%Y-%m-%dT%H:%M'),
            'data_hora_fim': (inicio + timedelta(hours=9)).strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertEqual(resp.status_code, 302)
        bloqueio = BloqueioAgenda.objects.get()
        self.assertIsNone(bloqueio.profissional_id)
        self.assertContains(self.client.get(reverse('aranha:admin_bloqueios')), 'Todos os profissionais')

