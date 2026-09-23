"""Booking publico — consentimento LGPD, termos, ficha de saude e preco com promocao.

Regressoes da rodada 2 (gap1-03/04/05, gap2-01/03, gap3-02/06).
"""
import json
import re
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.models import (
    AceiteTermo,
    Atendimento,
    Cliente,
    Feriado,
    FormularioAnamnese,
    LogAuditoria,
    Notificacao,
    Promocao,
    RespostaAnamnese,
    VersaoTermo,
)
from aranha_estetica.services.agendamento_service import AgendamentoService
from aranha_estetica.services.anamnese import validar_respostas
from aranha_estetica.utils.saude import alertas_saude

from .factories import criar_cliente, criar_procedimento, criar_profissional
from .test_confirmar_agendamento import slot_local, verificar_sessao

SCHEMA_SAUDE = [
    {'key': 'gestante', 'tipo': 'bool', 'label': 'Está gestante?', 'obrigatorio': True},
    {'key': 'alergias', 'tipo': 'text', 'label': 'Possui alergias?'},
    {'key': 'area', 'tipo': 'select', 'label': 'Área', 'opcoes': ['Testa', 'Olhos']},
]


def _lgpd_vigente():
    """Versao LGPD ativa (a data migration cria a v1.0; senao cria aqui)."""
    return VersaoTermo.lgpd_vigente() or VersaoTermo.objects.create(
        tipo='LGPD', titulo='Política de Privacidade', conteudo='Resumo da política.',
        versao='1.0', vigente_desde=timezone.localdate(),
    )


@override_settings(RATELIMIT_ENABLE=False)
class _BaseBooking(TestCase):
    preco = Decimal('150.00')

    def setUp(self):
        cache.clear()
        Feriado.objects.all().delete()
        self.prof = criar_profissional()
        self.proc = criar_procedimento(profissional=self.prof, preco=self.preco)
        self.datetime_str = slot_local(dias=3)
        self.url = reverse('aranha:confirmar_agendamento')

    def _post(self, headers=None, **overrides):
        data = {
            'nome': 'Dora Teste',
            'telefone': '17988887777',
            'data_nascimento': '1990-06-15',
            'procedimento': self.proc.pk,
            'profissional': self.prof.pk,
            'datetime': self.datetime_str,
            'aceite_politica': 'on',
        }
        data.update(overrides)
        verificar_sessao(self.client, str(data['telefone']))
        return self.client.post(self.url, data, **(headers or {}))

    def _ficha(self, obrigatorio=True, schema=None):
        return FormularioAnamnese.objects.create(
            nome='Ficha Toxina', tipo='ANAMNESE', escopo='PROCEDIMENTO', procedimento=self.proc,
            obrigatorio=obrigatorio, schema_json=schema or SCHEMA_SAUDE,
        )


class ValidarRespostasTests(TestCase):
    """gap2-03: validador unico da ficha (booking + link publico)."""

    def test_descarta_chave_fora_do_schema_e_normaliza_bool(self):
        limpas, erros = validar_respostas(SCHEMA_SAUDE, {
            'gestante': 'nao', 'alergias': ' Lidocaina ', 'inventada': {'x': 1},
        })
        self.assertEqual(erros, [])
        self.assertEqual(limpas, {'gestante': False, 'alergias': 'Lidocaina'})

    def test_valores_invalidos_geram_erro(self):
        casos = [
            {'gestante': 'talvez'},
            {'gestante': 'sim', 'alergias': {'aninhado': [1, 2]}},
            {'gestante': 'sim', 'alergias': 'A' * 5000},
            {'gestante': 'sim', 'area': 'Joelho'},
            {'gestante': ['sim']},
        ]
        for dados in casos:
            with self.subTest(dados=str(dados)[:60]):
                _limpas, erros = validar_respostas(SCHEMA_SAUDE, dados)
                self.assertTrue(erros)

    def test_obrigatorio_vazio_falha_mas_false_e_resposta(self):
        _l, erros = validar_respostas(SCHEMA_SAUDE, {'gestante': ''})
        self.assertTrue(erros)
        limpas, erros = validar_respostas(SCHEMA_SAUDE, {'gestante': False})
        self.assertEqual(erros, [])
        self.assertIs(limpas['gestante'], False)


class FichaSaudeBookingTests(_BaseBooking):
    """gap2-01/03 + gap1-03 (art. 11): ficha validada, consentida e visivel."""

    def test_ficha_so_com_consentimento_de_saude(self):
        form = self._ficha()
        respostas = json.dumps({str(form.pk): {'gestante': 'sim', 'alergias': 'Lidocaina'}})
        resp = self._post(anamnese_respostas=respostas)
        self.assertNotIn('sucesso', resp.url)
        self.assertEqual(RespostaAnamnese.objects.count(), 0)
        self.assertEqual(Atendimento.objects.count(), 0)

        resp = self._post(anamnese_respostas=respostas, consent_dados_saude='on')
        self.assertIn('sucesso', resp.url)
        atd = Atendimento.objects.get()
        ficha = RespostaAnamnese.objects.get()
        self.assertEqual(ficha.respostas_json, {'gestante': True, 'alergias': 'Lidocaina'})
        self.assertIsNotNone(ficha.respondida_em)
        # consentimento art. 11 registrado com data e IP
        log = LogAuditoria.objects.get(tabela='atendimento', registro_id=atd.pk, acao__contains='art. 11')
        self.assertEqual(log.ip_origem, '127.0.0.1')
        self.assertIn('texto', log.detalhes)
        # a alergia declarada chega a quem faz o procedimento
        textos = [a['valor'] for a in alertas_saude(atd.cliente)]
        self.assertIn('Lidocaina', textos)

    def test_conteudo_invalido_ou_grande_e_recusado(self):
        form = self._ficha()
        for respostas in (
            {str(form.pk): {'gestante': 'talvez'}},
            {str(form.pk): {'gestante': 'sim', 'alergias': {'aninhado': [1]}}},
            {str(form.pk): {'gestante': 'sim', 'alergias': 'A' * 25_000}},
        ):
            with self.subTest(respostas=str(respostas)[:50]):
                resp = self._post(anamnese_respostas=json.dumps(respostas), consent_dados_saude='on')
                self.assertNotIn('sucesso', resp.url)
        self.assertEqual(RespostaAnamnese.objects.count(), 0)
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_chave_extra_descartada_e_link_publico_nao_reabre(self):
        form = self._ficha()
        respostas = {str(form.pk): {'gestante': 'nao', 'inventada': 'x'}}
        self._post(anamnese_respostas=json.dumps(respostas), consent_dados_saude='on')
        ficha = RespostaAnamnese.objects.get()
        self.assertEqual(ficha.respostas_json, {'gestante': False})
        resp = self.client.get(reverse('aranha:anamnese_publica', args=[ficha.token]))
        self.assertRedirects(resp, reverse('aranha:anamnese_obrigado'), fetch_redirect_response=False)

    def test_ficha_opcional_em_branco_nao_exige_consentimento(self):
        form = self._ficha(obrigatorio=False)
        resp = self._post(anamnese_respostas=json.dumps({str(form.pk): {'alergias': ''}}))
        self.assertIn('sucesso', resp.url)
        self.assertEqual(RespostaAnamnese.objects.count(), 0)


class PoliticaETermosBookingTests(_BaseBooking):
    """gap1-03/04/05: aceite obrigatorio no wizard, com prova; sem Notificacao de termo."""

    def test_sem_aceite_da_politica_nada_e_gravado(self):
        resp = self._post(aceite_politica='')
        self.assertNotIn('sucesso', resp.url)
        self.assertFalse(Cliente.objects.filter(telefone='17988887777').exists())
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_aceite_lgpd_gravado_com_prova(self):
        lgpd = _lgpd_vigente()
        resp = self._post(headers={'HTTP_USER_AGENT': 'Navegador Teste/1.0'})
        self.assertIn('sucesso', resp.url)
        atd = Atendimento.objects.get()
        aceite = AceiteTermo.objects.get(cliente=atd.cliente, versao_termo=lgpd)
        self.assertEqual(aceite.ip, '127.0.0.1')
        self.assertEqual(aceite.user_agent, 'Navegador Teste/1.0')
        self.assertEqual(aceite.conteudo_sha256, lgpd.sha256_conteudo)

    def test_termo_do_procedimento_exigido_e_vinculado_ao_atendimento(self):
        termo = VersaoTermo.objects.create(
            tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo Toxina',
            conteudo='Riscos e cuidados.', versao='2', vigente_desde=timezone.localdate(),
        )
        resp = self._post()
        self.assertNotIn('sucesso', resp.url)
        self.assertEqual(Atendimento.objects.count(), 0)

        resp = self._post(**{f'aceite_termo_{termo.pk}': 'on'})
        self.assertIn('sucesso', resp.url)
        atd = Atendimento.objects.get()
        aceite = AceiteTermo.objects.get(versao_termo=termo)
        self.assertEqual(aceite.atendimento, atd)
        self.assertEqual(aceite.cliente, atd.cliente)

    def test_termo_geral_de_procedimento_tambem_e_exigido(self):
        VersaoTermo.objects.create(
            tipo='PROCEDIMENTO', procedimento=None, titulo='Termo geral',
            conteudo='Vale para todos.', versao='1', vigente_desde=timezone.localdate(),
        )
        resp = self._post()
        self.assertNotIn('sucesso', resp.url)
        self.assertEqual(Atendimento.objects.count(), 0)

    def test_termo_ja_aceito_nao_e_exigido_de_novo(self):
        termo = VersaoTermo.objects.create(
            tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo Toxina',
            conteudo='Riscos.', versao='1', vigente_desde=timezone.localdate(),
        )
        cli = criar_cliente(nome='Dora Teste', telefone='17988887777')
        AceiteTermo.registrar(cli, termo)
        resp = self._post()
        self.assertIn('sucesso', resp.url)

    @patch('aranha_estetica.views.booking_public._enfileirar_email')
    def test_booking_nao_cria_notificacao_nem_email_de_termo(self, enfileirar):
        _lgpd_vigente()
        termo = VersaoTermo.objects.create(
            tipo='PROCEDIMENTO', procedimento=self.proc, titulo='Termo Toxina',
            conteudo='Riscos.', versao='1', vigente_desde=timezone.localdate(),
        )
        resp = self._post(email='dora@example.com', **{f'aceite_termo_{termo.pk}': 'on'})
        self.assertIn('sucesso', resp.url)
        self.assertEqual(Notificacao.objects.count(), 0)
        funcoes = [c.args[0] for c in enfileirar.call_args_list]
        self.assertNotIn('enviar_termos_pendentes_email', funcoes)

    @patch('aranha_estetica.views.booking_public._enfileirar_email')
    def test_email_da_profissional_leva_so_link_revisar_do_dia_local(self, enfileirar):
        from aranha_estetica.models import Usuario
        Usuario.objects.create_user(
            email='dra@example.com', password='Senha-forte-123', nome='Dra. Ana',
            papel=Usuario.PAPEL_PROFISSIONAL, profissional=self.prof,
        )
        self._post()
        chamada = next(c for c in enfileirar.call_args_list
                       if c.args[0] == 'enviar_aprovacao_profissional_email')
        dados = chamada.args[2]
        self.assertNotIn('link_aprovar', dados)
        self.assertNotIn('link_rejeitar', dados)
        dia = (timezone.localdate() + timedelta(days=3)).isoformat()
        self.assertTrue(dados['link_revisar'].endswith(f'?data={dia}'))


class ConsentsComunicacaoTests(_BaseBooking):
    """gap1-03: nada pre-marcado; desmarcar revoga so com o estado do cadastro na tela."""

    def test_wizard_nao_pre_marca_whatsapp_e_exige_politica(self):
        html = self.client.get(reverse('aranha:agendamento_publico')).content.decode()
        tag_d1 = re.search(r'<input[^>]*name="consent_whatsapp_confirmacao"[^>]*>', html).group(0)
        self.assertNotIn('checked', tag_d1)
        tag_pol = re.search(r'<input[^>]*name="aceite_politica"[^>]*>', html).group(0)
        self.assertIn('required', tag_pol)
        self.assertIn(reverse('aranha:politica_privacidade'), html)

    def test_cliente_novo_sem_marcar_fica_sem_opt_in(self):
        self._post()
        cli = Cliente.objects.get(telefone='17988887777')
        self.assertFalse(cli.consent_whatsapp_confirmacao)

    def test_desmarcar_revoga_quando_sincronizado(self):
        antes = timezone.now() - timedelta(days=30)
        cli = criar_cliente(
            nome='Dora Teste', telefone='17988887777', consent_whatsapp_confirmacao=True,
            consent_whatsapp_confirmacao_em=antes,
        )
        self._post(consents_sincronizados='1')
        cli.refresh_from_db()
        self.assertFalse(cli.consent_whatsapp_confirmacao)
        self.assertGreater(cli.consent_whatsapp_confirmacao_em, antes)

    def test_sem_sincronizacao_post_nao_revoga(self):
        cli = criar_cliente(nome='Dora Teste', telefone='17988887777', consent_whatsapp_confirmacao=True)
        self._post()
        cli.refresh_from_db()
        self.assertTrue(cli.consent_whatsapp_confirmacao)


class PromocaoNoBookingTests(_BaseBooking):
    """gap3-02: o preco anunciado em /promocoes/ e o gravado no agendamento."""

    def _promo(self, **kw):
        hoje = timezone.localdate()
        dados = {
            'procedimento': self.proc, 'nome': 'Setembro Glow',
            'desconto_percentual': Decimal('20'),
            'data_inicio': hoje, 'data_fim': hoje + timedelta(days=30), 'ativa': True,
        }
        dados.update(kw)
        return Promocao.objects.create(**dados)

    def test_desconto_percentual_gravado_e_mostrado(self):
        promo = self._promo()
        resp = self._post()
        self.assertIn('sucesso', resp.url)
        atd = Atendimento.objects.get()
        self.assertEqual(atd.valor_cobrado, Decimal('120.00'))
        self.assertEqual(atd.valor_original, Decimal('150.00'))
        self.assertEqual(atd.promocao, promo)
        self.assertIn('Setembro Glow', atd.descricao_preco)
        dados = self.client.session['agendamento_sucesso']
        self.assertEqual(dados['valor'], 'R$ 120,00')
        self.assertEqual(dados['valor_cheio'], 'R$ 150,00')
        self.assertEqual(dados['promocao'], 'Setembro Glow')
        pagina = self.client.get(reverse('aranha:agendamento_sucesso')).content.decode()
        self.assertIn('R$ 120,00', pagina)
        self.assertIn('Setembro Glow', pagina)

    def test_preco_fixo(self):
        self._promo(desconto_percentual=Decimal('0'), preco_promocional=Decimal('99.00'))
        self._post()
        self.assertEqual(Atendimento.objects.get().valor_cobrado, Decimal('99.00'))

    def test_promo_inativa_expirada_ou_fora_da_data_do_atendimento(self):
        hoje = timezone.localdate()
        self._promo(ativa=False)
        self._promo(data_inicio=hoje - timedelta(days=10), data_fim=hoje - timedelta(days=1))
        # vale hoje, mas acaba antes do dia do atendimento (+3)
        self._promo(data_fim=hoje + timedelta(days=1))
        self._post()
        atd = Atendimento.objects.get()
        self.assertEqual(atd.valor_cobrado, Decimal('150.00'))
        self.assertIsNone(atd.promocao)
        self.assertIsNone(atd.valor_original)

    def test_promo_geral_de_preco_fixo_e_ignorada(self):
        self._promo(procedimento=None, desconto_percentual=Decimal('0'), preco_promocional=Decimal('10'))
        self._post()
        self.assertEqual(Atendimento.objects.get().valor_cobrado, Decimal('150.00'))

    def test_api_de_horarios_e_card_mostram_o_preco_promocional(self):
        self._promo()
        dia = (timezone.localdate() + timedelta(days=3)).isoformat()
        resp = self.client.get(reverse('aranha:api_horarios_disponiveis'),
                               {'data': dia, 'procedimento_id': self.proc.pk})
        prof = resp.json()['horarios'][0]['profissionais'][0]
        self.assertEqual(prof['valor'], '120.00')
        self.assertEqual(prof['valor_cheio'], '150.00')
        self.assertEqual(prof['promocao'], 'Setembro Glow')

        html = self.client.get(reverse('aranha:agendamento_publico')).content.decode()
        self.assertIn('proc-promo', html)
        self.assertIn('Setembro Glow', html)
        # data-proc-preco segue o cheio (fallback do resumo fora da promo)
        self.assertIn('data-proc-preco="150.0"', html)


class EmailAprovacaoRetornoTests(TestCase):
    """gap3-06: retorno gratuito nao vira 'A consultar' no e-mail de aprovacao."""

    def test_valor_do_retorno(self):
        prof = criar_profissional()
        proc = criar_procedimento(profissional=prof)
        cli = criar_cliente(email='cli@example.com')
        inicio = timezone.now() + timedelta(days=2)
        origem = Atendimento.objects.create(
            cliente=cli, profissional=prof, procedimento=proc, status='REALIZADO',
            data_hora_inicio=inicio - timedelta(days=20), data_hora_fim=inicio - timedelta(days=20, minutes=-30),
        )
        retorno = Atendimento.objects.create(
            cliente=cli, profissional=prof, procedimento=proc, status='PENDENTE',
            data_hora_inicio=inicio, data_hora_fim=inicio + timedelta(minutes=30),
            valor_cobrado=Decimal('0'), eh_retorno=True, atendimento_origem=origem,
        )
        with patch.object(AgendamentoService, '_enviar_email_confirmacao') as enviar, \
                self.captureOnCommitCallbacks(execute=True):
            AgendamentoService().aprovar(retorno)
        self.assertEqual(enviar.call_args.args[1]['valor'], 'Sem custo (retorno)')
