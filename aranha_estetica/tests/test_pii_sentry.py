"""Filtro de PII do Sentry (utils/pii): token de link, dado de saude e contato nao saem.

O Sentry e operador no exterior: erro e transacao amostrada passam pelos mesmos filtros.
"""
from unittest import skipUnless

from django.test import SimpleTestCase

from aranha_estetica.utils.pii import (
    mask_cpf,
    mask_email,
    mask_telefone,
    redigir_texto,
    redigir_tokens_url,
    sentry_before_breadcrumb,
    sentry_before_send,
    sentry_before_send_transaction,
)

try:
    import sentry_sdk  # noqa: F401
    TEM_SENTRY = True
except ImportError:  # pragma: no cover - o requirements instala
    TEM_SENTRY = False

TOKEN = 'Zq3xVx1lHkP0aBcDeFgHiJkLmNoPqRsT'


def _evento_erro():
    return {
        'exception': {'values': [{
            'type': 'IntegrityError',
            'value': ('duplicate key value violates unique constraint "uq_cliente_telefone"\n'
                      'DETAIL:  Key (telefone)=(17991234567) already exists.'),
            'stacktrace': {'frames': [{'function': 'prontuario_salvar',
                                       'vars': {'alergias': 'dipirona', 'cliente': 'Maria'}}]},
        }]},
        'logentry': {'message': 'falha ao avisar %s', 'formatted': 'falha ao avisar maria.silva@gmail.com',
                     'params': ['maria.silva@gmail.com']},
        'request': {
            'url': f'https://clinica.test/anamnese/{TOKEN}/',
            'query_string': f'token={TOKEN}',
            'data': {'telefone': '17991234567'},
            'headers': {'Referer': f'https://clinica.test/termo/{TOKEN}/'},
        },
        'breadcrumbs': {'values': [
            {'category': 'query', 'message': 'SELECT 1 FROM cliente WHERE telefone = %s',
             'data': {'db.params': ['17991234567']}},
            {'category': 'httplib', 'message': None,
             'data': {'url': f'https://clinica.test/nps/{TOKEN}/'}},
        ]},
        'transaction': f'/confirmar/{TOKEN}/',
    }


class FiltroEventoTests(SimpleTestCase):
    def test_erro_sai_sem_token_variaveis_locais_e_contato(self):
        ev = sentry_before_send(_evento_erro(), {})
        bruto = repr(ev)
        self.assertNotIn(TOKEN, bruto)
        self.assertNotIn('17991234567', bruto)
        self.assertNotIn('maria.silva', bruto)
        self.assertNotIn('dipirona', bruto)
        frame = ev['exception']['values'][0]['stacktrace']['frames'][0]
        self.assertNotIn('vars', frame)
        self.assertIn('Key (telefone)=([redigido])', ev['exception']['values'][0]['value'])
        self.assertEqual(ev['request']['data'], '[scrubbed]')
        self.assertNotIn('params', ev['logentry'])
        self.assertNotIn('data', ev['breadcrumbs']['values'][0])  # params do SQL
        self.assertEqual(ev['transaction'], '/confirmar/[token]/')

    def test_transacao_amostrada_passa_pelo_mesmo_filtro(self):
        ev = sentry_before_send_transaction({
            'type': 'transaction',
            'transaction': '/pesquisa/<str:token>/',
            'request': {'url': f'https://clinica.test/pesquisa/{TOKEN}/',
                        'query_string': '', 'headers': {}},
            'spans': [{'op': 'http.client', 'description': f'GET https://clinica.test/feed.ics?token={TOKEN}'}],
        }, {})
        self.assertNotIn(TOKEN, repr(ev))
        self.assertEqual(ev['request']['url'], 'https://clinica.test/pesquisa/[token]/')

    def test_breadcrumb_avulso(self):
        crumb = sentry_before_breadcrumb({'category': 'log', 'message': 'OTP para (17) 99123-4567'}, {})
        self.assertEqual(crumb['message'], 'OTP para [telefone]')

    def test_redigir_texto(self):
        self.assertEqual(redigir_texto('cpf 123.456.789-09'), 'cpf [cpf]')
        self.assertEqual(redigir_texto('fone 5517991234567'), 'fone [numero]')
        self.assertEqual(redigir_texto('ok id=42'), 'ok id=42')
        self.assertIsNone(redigir_texto(None))


@skipUnless(TEM_SENTRY, 'sentry-sdk ausente')
class OpcoesSentryTests(SimpleTestCase):
    def test_configuracao_de_producao(self):
        from aranha_estetica.utils.pii import opcoes_sentry

        op = opcoes_sentry('https://chave@o0.ingest.sentry.io/1')
        self.assertFalse(op['send_default_pii'])
        self.assertFalse(op['include_local_variables'])
        self.assertEqual(op['max_request_body_size'], 'never')
        self.assertIs(op['before_send'], sentry_before_send)
        self.assertIs(op['before_send_transaction'], sentry_before_send_transaction)
        self.assertIs(op['before_breadcrumb'], sentry_before_breadcrumb)

    def test_scrubber_remove_dado_de_saude_em_qualquer_nivel(self):
        from aranha_estetica.utils.pii import opcoes_sentry

        scrubber = opcoes_sentry('https://chave@o0.ingest.sentry.io/1')['event_scrubber']
        ev = {'extra': {'contexto': {'prontuario': {'alergias': 'dipirona', 'historico_saude': 'asma'},
                                     'ficha': {'respostas': {'gestante': True}}}}}
        scrubber.scrub_event(ev)
        bruto = repr(ev)
        self.assertNotIn('dipirona', bruto)
        self.assertNotIn('asma', bruto)
        self.assertNotIn('gestante', bruto)


class MascarasTests(SimpleTestCase):
    """Mascaras deterministas p/ log/Sentry: prefixo e sufixo p/ depurar, corpo oculto."""

    def test_email(self):
        self.assertEqual(mask_email('joao.silva@gmail.com'), 'jo***@gmail.com')
        self.assertEqual(mask_email(' ab@x.com '), 'a***@x.com')
        self.assertEqual(mask_email('sem-arroba'), '***')
        self.assertEqual(mask_email(None), '')

    def test_telefone(self):
        self.assertEqual(mask_telefone('(17) 99999-0000'), '17****0000')
        self.assertEqual(mask_telefone('123'), '***')
        self.assertEqual(mask_telefone(''), '')

    def test_cpf(self):
        self.assertEqual(mask_cpf('123.456.789-00'), '123*****00')
        self.assertEqual(mask_cpf('1234'), '***')
        self.assertEqual(mask_cpf(None), '')

    def test_tokens_url(self):
        self.assertEqual(redigir_tokens_url(f'/nps/{TOKEN}/'), '/nps/[token]/')
        self.assertEqual(redigir_tokens_url('/nps/obrigado/'), '/nps/obrigado/')
        self.assertEqual(redigir_tokens_url(None), '')
        # linha do access log: o token sai, o resto da linha (" HTTP/1.1" 200) fica
        self.assertEqual(redigir_tokens_url(f'"GET /confirmar/{TOKEN} HTTP/1.1" 200'), '"GET /confirmar/[token] HTTP/1.1" 200')
        self.assertEqual(redigir_tokens_url('"GET /admin-login/recuperar/ HTTP/1.1" 200'),
                         '"GET /admin-login/recuperar/ HTTP/1.1" 200')

    def test_extra_com_chave_pessoal_sai_mascarado(self):
        evento = sentry_before_send({'extra': {
            'email': 'maria.silva@gmail.com', 'cpf': '12345678900', 'nome': 'Maria',
            'telefone': '17991234567', 'destinatario': 'ana@x.com', 'tentativas': 3,
        }}, {})
        self.assertEqual(evento['extra'], {
            'email': 'ma***@gmail.com', 'cpf': '123*****00', 'nome': 'M***',
            'telefone': '17****4567', 'destinatario': 'an***@x.com', 'tentativas': 3,
        })
