"""Contrato de termos (rodada 2): AceiteTermo.registrar, VersaoTermo.lgpd_vigente
e imutabilidade da versao ja aceita (prova do texto — LGPD art. 8)."""
import hashlib

from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from aranha_estetica.models import AceiteTermo, VersaoTermo

from .factories import (
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional,
)


def _termo(tipo='LGPD', procedimento=None, versao='1.0', conteudo='Texto do termo', ativa=True):
    return VersaoTermo.objects.create(
        tipo=tipo, procedimento=procedimento, titulo=f'Termo {versao}', conteudo=conteudo,
        versao=versao, vigente_desde=timezone.localdate(), ativa=ativa,
    )


@override_settings(CLIENT_IP_HEADER='')
class RegistrarAceiteTests(TestCase):
    def setUp(self):
        self.cliente = criar_cliente()
        self.termo = _termo(conteudo='Política v1')
        self.rf = RequestFactory()

    def test_grava_ip_user_agent_e_hash_do_texto(self):
        prof = criar_profissional()
        at = criar_atendimento(self.cliente, prof, criar_procedimento(profissional=prof))
        req = self.rf.post('/x/', REMOTE_ADDR='200.10.20.30', HTTP_USER_AGENT='Navegador/1.0')
        aceite = AceiteTermo.registrar(self.cliente, self.termo, req, atendimento=at)
        aceite.refresh_from_db()
        self.assertEqual(aceite.ip, '200.10.20.30')
        self.assertEqual(aceite.user_agent, 'Navegador/1.0')
        self.assertEqual(aceite.atendimento_id, at.pk)
        self.assertEqual(aceite.conteudo_sha256, hashlib.sha256('Política v1'.encode()).hexdigest())

    def test_idempotente_mantem_a_prova_do_primeiro_aceite(self):
        primeiro = AceiteTermo.registrar(
            self.cliente, self.termo, self.rf.post('/x/', REMOTE_ADDR='10.0.0.1', HTTP_USER_AGENT='A'),
        )
        segundo = AceiteTermo.registrar(
            self.cliente, self.termo, self.rf.post('/x/', REMOTE_ADDR='10.0.0.2', HTTP_USER_AGENT='B'),
        )
        self.assertEqual(primeiro.pk, segundo.pk)
        self.assertEqual(AceiteTermo.objects.filter(cliente=self.cliente).count(), 1)
        segundo.refresh_from_db()
        self.assertEqual((segundo.ip, segundo.user_agent), ('10.0.0.1', 'A'))

    def test_sem_versao_vigente_nao_grava(self):
        self.assertIsNone(AceiteTermo.registrar(self.cliente, None, self.rf.post('/x/')))
        self.assertFalse(AceiteTermo.objects.exists())

    def test_sem_request_e_user_agent_longo(self):
        aceite = AceiteTermo.registrar(self.cliente, self.termo)
        self.assertIsNone(aceite.ip)
        self.assertEqual(aceite.user_agent, '')
        outro = criar_cliente(telefone='11988887777')
        req = self.rf.post('/x/', REMOTE_ADDR='10.0.0.9', HTTP_USER_AGENT='x' * 900)
        self.assertEqual(len(AceiteTermo.registrar(outro, self.termo, req).user_agent), 500)

    def test_ip_invalido_vira_nulo_e_nao_ip_inventado(self):
        req = self.rf.post('/x/', REMOTE_ADDR='lixo')
        self.assertIsNone(AceiteTermo.registrar(self.cliente, self.termo, req).ip)


class LgpdVigenteTests(TestCase):
    def test_devolve_so_a_lgpd_global_ativa(self):
        self.assertIsNone(VersaoTermo.lgpd_vigente())
        _termo(versao='0.9', ativa=False)
        _termo(tipo='PROCEDIMENTO', procedimento=criar_procedimento(), versao='1.0')
        self.assertIsNone(VersaoTermo.lgpd_vigente())
        vigente = _termo(versao='1.0')
        self.assertEqual(VersaoTermo.lgpd_vigente(), vigente)


class VersaoTermoImutavelTests(TestCase):
    def setUp(self):
        self.termo = _termo(conteudo='Texto aceito')

    def test_sem_aceite_ainda_pode_corrigir(self):
        self.termo.conteudo = 'Texto corrigido antes de publicar'
        self.termo.full_clean()
        self.termo.save()
        self.termo.refresh_from_db()
        self.assertEqual(self.termo.conteudo, 'Texto corrigido antes de publicar')

    def test_com_aceite_texto_e_versao_congelados(self):
        AceiteTermo.registrar(criar_cliente(), self.termo)
        for campo, valor in (('conteudo', 'Texto trocado'), ('titulo', 'Outro'), ('versao', '9.9'),
                             ('tipo', 'PROCEDIMENTO')):
            termo = VersaoTermo.objects.get(pk=self.termo.pk)
            setattr(termo, campo, valor)
            with self.assertRaises(ValidationError, msg=campo):
                termo.save()
            with self.assertRaises(ValidationError, msg=campo):
                termo.clean()
        self.assertEqual(VersaoTermo.objects.get(pk=self.termo.pk).conteudo, 'Texto aceito')

    def test_com_aceite_ainda_pode_desativar(self):
        AceiteTermo.registrar(criar_cliente(), self.termo)
        self.termo.ativa = False
        self.termo.save()
        self.assertFalse(VersaoTermo.objects.get(pk=self.termo.pk).ativa)
        self.termo.ativa = True
        self.termo.save(update_fields=['ativa'])
        self.assertTrue(VersaoTermo.objects.get(pk=self.termo.pk).ativa)
