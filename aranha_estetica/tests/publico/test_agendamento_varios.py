"""Pedido pelo site com varios tratamentos, regras de qualidade dos dados e ficha sim/nao (2026-11-24).

- ``agendamento_online.solicitar_varios``: um atendimento por tratamento, tudo ou nada; recusa
  tratamento repetido, horarios que se sobrepoem (com o buffer da mesma profissional) e pedido acima
  do teto;
- ``/ajax/horarios-disponiveis/?carrinho=``: os horarios que batem com os outros tratamentos do
  pedido nao sao oferecidos;
- form: ``itens_extras``, nome completo, idade maxima, DDD existente e numero de enfeite;
- opt-ins de comunicacao NAO sao mais gravados no agendamento (so em Minhas preferencias);
- anamnese: pergunta condicional (``se``) e texto que nao e resposta.
"""
import json
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aranha_estetica.conteudo.anamnese import ANAMNESE_PADRAO, ANAMNESE_PADRAO_ANTIGA
from aranha_estetica.constants import MAX_ATIVOS_ONLINE, MAX_TRATAMENTOS_POR_PEDIDO
from aranha_estetica.excecoes import RegraDeNegocio
from aranha_estetica.models import Atendimento, Cliente, Consentimento, FormularioAnamnese, RespostaAnamnese
from aranha_estetica.services import agendamento_online
from aranha_estetica.services.agendamento_online import ItemPedido, PedidoAgendamento
from aranha_estetica.services.anamnese import texto_plausivel, validar_respostas
from aranha_estetica.utils import telefone as telefone_br
from aranha_estetica.validators import validate_nome_completo

from ..factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional, limpar_feriados

TELEFONE = '17999992222'


def inicio_local(dias=3, hora=10, minuto=0):
    dia = timezone.localdate() + timedelta(days=dias)
    return timezone.make_aware(datetime.combine(dia, time(hora, minuto)))


def verificar_sessao(client, telefone):
    session = client.session
    session['cliente_telefone'] = telefone
    session['cliente_verificado_em'] = (timezone.now() - timedelta(minutes=1)).isoformat()
    session.save()


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        limpar_feriados()
        self.ana = criar_profissional('Dra. Ana')
        self.bia = criar_profissional('Bia Esteticista')
        self.botox = criar_procedimento('Botox', duracao=30, profissional=self.ana, preco=Decimal('300.00'))
        self.limpeza = criar_procedimento('Limpeza', duracao=60, profissional=self.bia, preco=Decimal('130.00'))

    def pedido(self, *extras, inicio=None, **campos):
        dados = {
            'nome': 'Maria Teste', 'telefone': TELEFONE, 'data_nascimento': datetime(1990, 6, 15).date(),
            'email': None, 'procedimento': self.botox, 'profissional': self.ana,
            'inicio': inicio or inicio_local(), 'extras': tuple(extras),
        }
        dados.update(campos)
        return PedidoAgendamento(**dados)


class SolicitarVariosTests(_Base):
    def test_dois_tratamentos_viram_dois_atendimentos_da_mesma_cliente(self):
        pedido = self.pedido(ItemPedido(self.limpeza, self.bia, inicio_local(hora=10, minuto=30)))
        atendimentos = agendamento_online.solicitar_varios(pedido)

        self.assertEqual([a.procedimento for a in atendimentos], [self.botox, self.limpeza])
        self.assertEqual(Cliente.objects.filter(telefone=TELEFONE).count(), 1)
        self.assertEqual({a.cliente_id for a in atendimentos}, {Cliente.objects.get(telefone=TELEFONE).pk})
        self.assertEqual({a.status for a in atendimentos}, {Atendimento.STATUS_PENDENTE})
        self.assertEqual([a.valor_cobrado for a in atendimentos], [Decimal('300.00'), Decimal('130.00')])

    def test_solicitar_segue_devolvendo_o_primeiro_atendimento(self):
        atendimento = agendamento_online.solicitar(self.pedido())
        self.assertEqual(atendimento.procedimento, self.botox)
        self.assertEqual(Atendimento.objects.count(), 1)

    def test_horarios_sobrepostos_recusam_o_pedido_inteiro(self):
        """A cliente nao faz dois tratamentos ao mesmo tempo, mesmo com profissionais diferentes."""
        pedido = self.pedido(ItemPedido(self.limpeza, self.bia, inicio_local(hora=10, minuto=0)))
        with self.assertRaises(RegraDeNegocio) as recusa:
            agendamento_online.solicitar_varios(pedido)
        self.assertEqual(recusa.exception.codigo, 'horarios_sobrepostos')
        self.assertFalse(Atendimento.objects.exists())
        self.assertFalse(Cliente.objects.filter(telefone=TELEFONE).exists())

    def test_mesma_profissional_respeita_o_intervalo_entre_sessoes(self):
        self.botox.buffer_minutos = 30
        self.botox.save(update_fields=['buffer_minutos'])
        peeling = criar_procedimento('Peeling', duracao=30, profissional=self.ana)
        colado = self.pedido(ItemPedido(peeling, self.ana, inicio_local(hora=10, minuto=30)))
        with self.assertRaises(RegraDeNegocio) as recusa:
            agendamento_online.solicitar_varios(colado)
        self.assertEqual(recusa.exception.codigo, 'horarios_sobrepostos')

        com_folga = self.pedido(ItemPedido(peeling, self.ana, inicio_local(hora=11, minuto=0)))
        self.assertEqual(len(agendamento_online.solicitar_varios(com_folga)), 2)

    def test_tratamento_repetido_e_recusado(self):
        pedido = self.pedido(ItemPedido(self.botox, self.ana, inicio_local(hora=14)))
        with self.assertRaises(RegraDeNegocio) as recusa:
            agendamento_online.solicitar_varios(pedido)
        self.assertEqual(recusa.exception.codigo, 'tratamento_repetido')

    def test_acima_do_teto_de_tratamentos_e_recusado(self):
        extras = [
            ItemPedido(criar_procedimento(f'Extra {i}', duracao=30, profissional=self.bia), self.bia, inicio_local(hora=11 + i))
            for i in range(MAX_TRATAMENTOS_POR_PEDIDO)
        ]
        with self.assertRaises(RegraDeNegocio) as recusa:
            agendamento_online.solicitar_varios(self.pedido(*extras))
        self.assertEqual(recusa.exception.codigo, 'tratamentos_demais')

    def test_pedido_que_passaria_do_teto_de_horarios_em_aberto_e_recusado(self):
        cliente = criar_cliente(nome='Maria Teste', telefone=TELEFONE)
        outro = criar_procedimento('Massagem', duracao=30, profissional=self.bia)
        for i in range(MAX_ATIVOS_ONLINE - 1):
            criar_atendimento(cliente, self.bia, outro, data_hora=inicio_local(dias=5 + i, hora=9))
        pedido = self.pedido(ItemPedido(self.limpeza, self.bia, inicio_local(hora=11)))
        with self.assertRaises(RegraDeNegocio) as recusa:
            agendamento_online.solicitar_varios(pedido)
        self.assertEqual(recusa.exception.codigo, 'limite_online')
        self.assertEqual(Atendimento.objects.filter(cliente=cliente).count(), MAX_ATIVOS_ONLINE - 1)

    def test_segundo_horario_ocupado_nao_deixa_o_primeiro_gravado(self):
        """Tudo ou nada: o 2o tratamento sem vaga desfaz o pedido inteiro."""
        outra = criar_cliente(nome='Outra Cliente', telefone='17988887777')
        criar_atendimento(outra, self.bia, self.limpeza, data_hora=inicio_local(hora=11))
        pedido = self.pedido(ItemPedido(self.limpeza, self.bia, inicio_local(hora=11)))
        with self.assertRaises(RegraDeNegocio):
            agendamento_online.solicitar_varios(pedido)
        self.assertFalse(Atendimento.objects.filter(cliente__telefone=TELEFONE).exists())

    def test_ficha_do_pedido_e_gravada_uma_vez(self):
        ficha = FormularioAnamnese.objects.create(
            nome='Anamnese padrão', tipo='ANAMNESE', escopo='GLOBAL', schema_json=ANAMNESE_PADRAO,
            ativo=True, obrigatorio=True,
        )
        respostas = {str(ficha.pk): {
            'gestante_ou_amamentando': 'nao', 'tem_alergia': 'sim', 'alergias': 'Lidocaína',
            'uso_de_acidos': 'nao', 'fez_procedimento_recente': 'nao',
        }}
        pedido = self.pedido(
            ItemPedido(self.limpeza, self.bia, inicio_local(hora=11)), anamneses=respostas, consent_saude=True,
        )
        primeiro, _segundo = agendamento_online.solicitar_varios(pedido)
        gravadas = RespostaAnamnese.objects.filter(formulario=ficha)
        self.assertEqual(gravadas.count(), 1)
        self.assertEqual(gravadas.get().atendimento, primeiro)
        self.assertEqual(gravadas.get().respostas_json['alergias'], 'Lidocaína')

    def test_resumo_do_pedido_lista_os_tratamentos_em_ordem_de_horario(self):
        pedido = self.pedido(ItemPedido(self.limpeza, self.bia, inicio_local(hora=9)))
        dados = agendamento_online.resumo_do_pedido(agendamento_online.solicitar_varios(pedido))
        self.assertEqual([item['procedimento'] for item in dados['itens']], ['Limpeza', 'Botox'])
        self.assertEqual(dados['procedimento'], 'Limpeza')
        self.assertTrue(dados['pendente'])


@override_settings(RATELIMIT_ENABLE=False)
class ConfirmarVariosViewTests(_Base):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.url = reverse('aranha:confirmar_agendamento')

    def _post(self, **overrides):
        dados = {
            'nome': 'Maria Teste', 'telefone': TELEFONE, 'data_nascimento': '1990-06-15',
            'procedimento': self.botox.pk, 'profissional': self.ana.pk, 'datetime': inicio_local().isoformat(),
            'aceite_politica': 'on',
        }
        dados.update(overrides)
        verificar_sessao(self.client, TELEFONE)
        return self.client.post(self.url, dados)

    def _extras(self, *itens):
        return json.dumps([
            {'procedimento': proc.pk, 'profissional': prof.pk, 'datetime': inicio.isoformat()}
            for proc, prof, inicio in itens
        ])

    def test_post_com_itens_extras_cria_os_dois_e_o_sucesso_lista_ambos(self):
        resp = self._post(itens_extras=self._extras((self.limpeza, self.bia, inicio_local(hora=10, minuto=30))))
        self.assertRedirects(resp, reverse('aranha:agendamento_sucesso'), fetch_redirect_response=False)
        self.assertEqual(Atendimento.objects.count(), 2)

        pagina = self.client.get(reverse('aranha:agendamento_sucesso'))
        self.assertContains(pagina, 'Resumo dos 2 agendamentos')
        self.assertContains(pagina, 'Botox')
        self.assertContains(pagina, 'Limpeza')
        self.assertContains(pagina, 'id="atalho-preferencias"')

    def test_itens_extras_malformado_recusa_sem_gravar_nada(self):
        for lixo in ('{', '"texto"', '[1, 2]', json.dumps([{'procedimento': 999999, 'profissional': self.bia.pk,
                                                             'datetime': inicio_local(hora=12).isoformat()}])):
            with self.subTest(lixo=lixo):
                resp = self._post(itens_extras=lixo)
                self.assertRedirects(resp, reverse('aranha:agendamento_publico'), fetch_redirect_response=False)
                self.assertFalse(Atendimento.objects.exists())

    def test_opt_ins_de_comunicacao_nao_sao_gravados_no_agendamento(self):
        """Decisao do dono (2026-11-24): os avisos ficam so em Meus agendamentos > Minhas preferencias."""
        resp = self._post(consent_email_marketing='on', consent_whatsapp_confirmacao='on', consent_whatsapp_nps='on')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Atendimento.objects.count(), 1)
        self.assertFalse(Consentimento.objects.exists())

    def test_wizard_nao_mostra_os_checkboxes_de_comunicacao(self):
        html = self.client.get(reverse('aranha:agendamento_publico')).content.decode()
        self.assertNotIn('consent_whatsapp_confirmacao', html)
        self.assertNotIn('consent_email_marketing', html)
        self.assertIn('id="form-itens-extras"', html)
        self.assertIn('id="tpl-agenda-item"', html)
        self.assertIn('id="carrinho-barra"', html)

    def test_recusa_do_servidor_aparece_dentro_do_cartao_do_wizard(self):
        resp = self._post(nome='Maria')   # so o primeiro nome
        self.assertRedirects(resp, reverse('aranha:agendamento_publico'), fetch_redirect_response=False)
        html = self.client.get(reverse('aranha:agendamento_publico')).content.decode()
        self.assertIn('id="wizard-erro-servidor"', html)
        self.assertIn('nome completo', html)
        self.assertFalse(Atendimento.objects.exists())

    def test_regras_de_qualidade_dos_dados(self):
        hoje = timezone.localdate()
        casos = {
            'nome so com o primeiro nome': {'nome': 'Maria'},
            'nome com numero': {'nome': 'Maria 123'},
            'nome de enfeite': {'nome': 'Aaaa Bbbb'},
            'nascimento hoje': {'data_nascimento': hoje.isoformat()},
            'nascimento no futuro': {'data_nascimento': (hoje + timedelta(days=1)).isoformat()},
            'idade impossivel': {'data_nascimento': '1850-01-01'},
            'e-mail sem dominio': {'email': 'maria@'},
        }
        for nome, campos in casos.items():
            with self.subTest(caso=nome):
                resp = self._post(**campos)
                self.assertRedirects(resp, reverse('aranha:agendamento_publico'), fetch_redirect_response=False)
                self.assertFalse(Atendimento.objects.exists())

    def test_cadastro_existente_com_nome_incompleto_continua_agendando(self):
        """D16: o nome do POST e ignorado p/ quem ja tem cadastro — nao pode barrar a titular antiga."""
        criar_cliente(nome='Stefany', telefone=TELEFONE)
        resp = self._post(nome='Stefany')
        self.assertRedirects(resp, reverse('aranha:agendamento_sucesso'), fetch_redirect_response=False)
        self.assertEqual(Atendimento.objects.count(), 1)


@override_settings(RATELIMIT_ENABLE=False)
class HorariosComCarrinhoTests(_Base):
    def _horarios(self, procedimento, carrinho=None):
        dia = timezone.localdate() + timedelta(days=3)
        params = {'data': dia.isoformat(), 'procedimento_id': procedimento.pk}
        if carrinho is not None:
            params['carrinho'] = json.dumps(carrinho)
        resp = self.client.get(reverse('aranha:api_horarios_disponiveis'), params)
        self.assertEqual(resp.status_code, 200)
        return {slot['horario']: [p['id'] for p in slot['profissionais']] for slot in resp.json()['horarios']}

    def test_horario_que_bate_com_outro_tratamento_do_pedido_some_da_lista(self):
        sem = self._horarios(self.limpeza)
        self.assertIn('10:00', sem)
        self.assertIn('09:30', sem)
        carrinho = [{'p': self.botox.pk, 'f': self.ana.pk, 'i': inicio_local(hora=10).isoformat()}]
        com = self._horarios(self.limpeza, carrinho)
        # Botox 10:00-10:30: a limpeza de 1h nao pode comecar 09:30 nem 10:00; 09:00 e 10:30 seguem
        self.assertNotIn('09:30', com)
        self.assertNotIn('10:00', com)
        self.assertIn('09:00', com)
        self.assertIn('10:30', com)

    def test_mesma_profissional_soma_o_intervalo_entre_sessoes(self):
        self.botox.buffer_minutos = 30
        self.botox.save(update_fields=['buffer_minutos'])
        peeling = criar_procedimento('Peeling', duracao=30, profissional=self.ana)
        carrinho = [{'p': self.botox.pk, 'f': self.ana.pk, 'i': inicio_local(hora=10).isoformat()}]
        com = self._horarios(peeling, carrinho)
        self.assertNotIn('10:30', com)   # dentro do intervalo depois do botox
        self.assertIn('11:00', com)

    def test_carrinho_malformado_e_ignorado(self):
        completo = self._horarios(self.limpeza)
        for lixo in ('{', '[]', '[{"p": "x"}]', '[{"p": %d, "f": %d, "i": "ontem"}]' % (self.botox.pk, self.ana.pk)):
            with self.subTest(lixo=lixo):
                dia = timezone.localdate() + timedelta(days=3)
                resp = self.client.get(reverse('aranha:api_horarios_disponiveis'),
                                       {'data': dia.isoformat(), 'procedimento_id': self.limpeza.pk, 'carrinho': lixo})
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(len(resp.json()['horarios']), len(completo))


class TelefoneQualidadeTests(SimpleTestCase):
    def test_ddd_que_nao_existe_e_recusado(self):
        for numero in ('20912345678', '10999990000', '00999990000', '2333334444'):
            with self.subTest(numero=numero):
                self.assertEqual(telefone_br.valido_ou_vazio(numero), '')
                self.assertFalse(telefone_br.eh_celular(numero))

    def test_ddd_valido_passa(self):
        self.assertEqual(telefone_br.valido_ou_vazio('(11) 91234-5678'), '11912345678')
        self.assertTrue(telefone_br.eh_celular('55 99 91234-5678'[3:]))
        self.assertEqual(len(telefone_br.DDDS_VALIDOS), 67)

    def test_numero_de_enfeite_nao_recebe_sms(self):
        for numero in ('11999999999', '17900000000', '21911111111'):
            with self.subTest(numero=numero):
                self.assertFalse(telefone_br.eh_celular(numero))
        self.assertTrue(telefone_br.eh_celular('11999990001'))


class NomeCompletoTests(SimpleTestCase):
    def test_nomes_de_verdade_passam(self):
        for nome in ('Maria Silva', "Ana D'Ávila", 'João Pedro de Souza', 'Ana-Clara Lima', 'José Carlos Jr.',
                     'João Paulo III'):
            with self.subTest(nome=nome):
                validate_nome_completo(nome)

    def test_o_que_nao_e_nome_e_recusado(self):
        for nome in ('Maria', 'M S', 'Maria 123', 'asdf@', 'Aaaa Bbbb', '', '   ', '...'):
            with self.subTest(nome=nome), self.assertRaises(ValidationError):
                validate_nome_completo(nome)


class AnamneseCondicionalTests(SimpleTestCase):
    def test_detalhe_so_e_exigido_quando_a_pergunta_de_controle_e_sim(self):
        base = {'gestante_ou_amamentando': 'nao', 'uso_de_acidos': 'nao', 'fez_procedimento_recente': 'nao'}
        limpas, erros = validar_respostas(ANAMNESE_PADRAO, dict(base, tem_alergia='nao'))
        self.assertEqual(erros, [])
        self.assertNotIn('alergias', limpas)

        _limpas, erros = validar_respostas(ANAMNESE_PADRAO, dict(base, tem_alergia='sim'))
        self.assertEqual(len(erros), 1)
        self.assertIn('A que você tem alergia?', erros[0])

        limpas, erros = validar_respostas(ANAMNESE_PADRAO, dict(base, tem_alergia='sim', alergias='Dipirona'))
        self.assertEqual(erros, [])
        self.assertEqual(limpas['alergias'], 'Dipirona')

    def test_detalhe_de_um_nao_e_descartado(self):
        limpas, erros = validar_respostas(ANAMNESE_PADRAO, {
            'gestante_ou_amamentando': 'nao', 'tem_alergia': 'nao', 'alergias': 'texto que nao vale',
            'uso_de_acidos': 'nao', 'fez_procedimento_recente': 'nao',
        })
        self.assertEqual(erros, [])
        self.assertNotIn('alergias', limpas)

    def test_ficha_em_branco_lista_as_perguntas_sim_nao(self):
        _limpas, erros = validar_respostas(ANAMNESE_PADRAO, {})
        self.assertEqual(len(erros), 4)   # os dois detalhes ("se") nao entram

    def test_texto_que_nao_e_resposta_e_recusado(self):
        for lixo in ('.', '---', '123', 'aaaa', 'KKKKK', '?', ' x '):
            with self.subTest(lixo=lixo):
                self.assertFalse(texto_plausivel(lixo.strip()))
        for resposta in ('não', 'ok', 'AAS', 'Lidocaína', 'Botox há 3 meses'):
            with self.subTest(resposta=resposta):
                self.assertTrue(texto_plausivel(resposta))
        _limpas, erros = validar_respostas([{'key': 'obs', 'tipo': 'text', 'label': 'Observação'}], {'obs': '...'})
        self.assertEqual(len(erros), 1)

    def test_schema_antigo_segue_valido_com_respostas_antigas(self):
        limpas, erros = validar_respostas(ANAMNESE_PADRAO_ANTIGA, {'gestante_ou_amamentando': 'nao', 'alergias': 'Dipirona'})
        self.assertEqual(erros, [])
        self.assertEqual(limpas, {'gestante_ou_amamentando': False, 'alergias': 'Dipirona'})


class SchemaCondicionalModelTests(TestCase):
    def _ficha(self, schema):
        return FormularioAnamnese(nome='Ficha', tipo='ANAMNESE', escopo='GLOBAL', schema_json=schema)

    def test_ficha_padrao_e_valida(self):
        self._ficha(ANAMNESE_PADRAO).full_clean()

    def test_se_precisa_apontar_para_pergunta_bool_anterior(self):
        invalidos = (
            [{'key': 'qual', 'tipo': 'text', 'label': 'Qual?', 'se': 'tem'}],                      # controle nao existe
            [{'key': 'qual', 'tipo': 'text', 'label': 'Qual?', 'se': 'tem'},
             {'key': 'tem', 'tipo': 'bool', 'label': 'Tem?'}],                                   # controle vem depois
            [{'key': 'tem', 'tipo': 'text', 'label': 'Tem?'},
             {'key': 'qual', 'tipo': 'text', 'label': 'Qual?', 'se': 'tem'}],                      # controle nao e bool
        )
        for schema in invalidos:
            with self.subTest(schema=schema), self.assertRaises(ValidationError):
                self._ficha(schema).full_clean()
