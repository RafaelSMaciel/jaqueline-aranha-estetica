"""Ator (aranha_estetica/ator.py): autor/IP/origem da borda lidos pelo registrador unico."""
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from aranha_estetica.ator import Ator, ator_atual, atuando, ip_valido
from aranha_estetica.middleware import ContextoBancoMiddleware
from aranha_estetica.models import LogAuditoria, Usuario
from aranha_estetica.utils.audit import registrar_log

from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional


class AtorTests(TestCase):
    def setUp(self):
        self.equipe = Usuario.objects.create_user(email='ator@x.com', password='senha-1-forte', nome='Ator')

    def test_registrar_log_le_autor_e_ip_do_ator(self):
        with atuando(Ator(usuario=self.equipe, ip='203.0.113.5', origem='web')):
            registrar_log(None, 'acao qualquer', 'cliente', 1, {'email': 'maria@exemplo.com'})
        log = LogAuditoria.objects.get(acao='acao qualquer')
        self.assertEqual((log.usuario_id, log.ip_origem), (self.equipe.pk, '203.0.113.5'))
        self.assertNotIn('maria@exemplo.com', str(log.detalhes))   # PII mascarada

    def test_explicito_ganha_do_ator(self):
        outro = Usuario.objects.create_user(email='outro@x.com', password='senha-1-forte', nome='Outro')
        with atuando(Ator(usuario=self.equipe, ip='203.0.113.5')):
            registrar_log(outro, 'explicito')
        self.assertEqual(LogAuditoria.objects.get(acao='explicito').usuario_id, outro.pk)

    def test_aninhado_volta_ao_de_fora(self):
        web = Ator(usuario=self.equipe, ip='203.0.113.5', origem='web')
        with atuando(web):
            with atuando(Ator(ip='192.0.2.1', origem='cron:x')):
                self.assertEqual(ator_atual().origem, 'cron:x')
            self.assertEqual(ator_atual(), web)
        self.assertEqual(ator_atual(), Ator())

    def test_middleware_abre_o_ator_do_request(self):
        visto = {}
        request = RequestFactory().get('/painel/', REMOTE_ADDR='203.0.113.50')
        request.user = self.equipe
        ContextoBancoMiddleware(lambda r: visto.setdefault('ator', ator_atual()))(request)
        self.assertEqual(visto['ator'], Ator(usuario=self.equipe, ip='203.0.113.50', origem='web'))
        self.assertEqual(ator_atual(), Ator())

    def test_anonimo_e_ip_desconhecido(self):
        request = RequestFactory().get('/', REMOTE_ADDR='')
        request.user = AnonymousUser()
        self.assertEqual(Ator.do_request(request), Ator(origem='web'))
        self.assertIsNone(ip_valido('0.0.0.0'))
        self.assertIsNone(ip_valido('nao-e-ip'))

    def test_transicao_da_fsm_grava_o_ip_da_borda(self):
        prof = criar_profissional()
        at = criar_atendimento(criar_cliente(), prof, criar_procedimento(profissional=prof))
        with atuando(Ator(ip='198.51.100.7', origem='web')):
            at.confirmar()
        log = LogAuditoria.objects.filter(tabela='atendimento', registro_id=at.pk).latest('pk')
        self.assertEqual(log.ip_origem, '198.51.100.7')
        self.assertIsNone(log.usuario_id)
