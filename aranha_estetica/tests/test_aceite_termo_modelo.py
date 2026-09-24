"""Contrato de termos (rodada 2): AceiteTermo.registrar, VersaoTermo.lgpd_vigente
/saude_vigente e imutabilidade da versao ja aceita (prova do texto — LGPD art. 8).
Postgres: triggers de imutabilidade da 0046 (QuerySet.update/SQL cru)."""
import hashlib
import unittest

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from aranha_estetica.constants import TERMO_LGPD_CONTEUDO
from aranha_estetica.models import AceiteTermo, VersaoTermo
from aranha_estetica.models.termos import TEXTO_CONSENTIMENTO_SAUDE

from .factories import (
    criar_atendimento, criar_cliente, criar_procedimento, criar_profissional,
)


def _termo(tipo='LGPD', procedimento=None, versao='1.0', conteudo='Texto do termo', ativa=True):
    if ativa:  # 1 ativa por escopo: arquiva a vigente (ex.: LGPD/SAUDE v1.0 das migrations)
        VersaoTermo.objects.filter(tipo=tipo, procedimento=procedimento, ativa=True).update(ativa=False)
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
    def test_migration_publica_lgpd_v1_mesmo_em_banco_novo(self):
        """0045: banco sem clientes (instalacao limpa) ja nasce com o termo LGPD."""
        termo = VersaoTermo.lgpd_vigente()
        self.assertIsNotNone(termo)
        self.assertEqual(termo.conteudo, TERMO_LGPD_CONTEUDO)

    def test_devolve_so_a_lgpd_global_ativa(self):
        VersaoTermo.objects.filter(tipo='LGPD').update(ativa=False)
        self.assertIsNone(VersaoTermo.lgpd_vigente())
        _termo(versao='0.9', ativa=False)
        _termo(tipo='PROCEDIMENTO', procedimento=criar_procedimento(), versao='1.0')
        self.assertIsNone(VersaoTermo.lgpd_vigente())
        vigente = _termo(versao='1.0')
        self.assertEqual(VersaoTermo.lgpd_vigente(), vigente)


class SaudeVigenteTests(TestCase):
    """Contrato 1: consentimento art. 11 como VersaoTermo SAUDE (0047)."""

    def test_migration_publica_saude_v1_com_o_texto_do_wizard(self):
        termo = VersaoTermo.saude_vigente()
        self.assertIsNotNone(termo)
        self.assertEqual((termo.tipo, termo.versao), ('SAUDE', '1.0'))
        self.assertIsNone(termo.procedimento_id)
        self.assertEqual(termo.conteudo, TEXTO_CONSENTIMENTO_SAUDE)
        self.assertEqual(VersaoTermo.texto_saude_vigente(), (termo, TEXTO_CONSENTIMENTO_SAUDE))

    def test_devolve_so_a_saude_ativa_e_nao_mistura_com_lgpd(self):
        VersaoTermo.objects.filter(tipo='SAUDE').update(ativa=False)
        self.assertIsNone(VersaoTermo.saude_vigente())
        # sem versao ativa: texto padrao p/ exibir, nada p/ registrar
        self.assertEqual(VersaoTermo.texto_saude_vigente(), (None, TEXTO_CONSENTIMENTO_SAUDE))
        self.assertEqual(VersaoTermo.lgpd_vigente().tipo, 'LGPD')
        nova = _termo(tipo='SAUDE', versao='2.0', conteudo='Texto novo art. 11')
        self.assertEqual(VersaoTermo.saude_vigente(), nova)
        self.assertEqual(VersaoTermo.texto_saude_vigente(), (nova, 'Texto novo art. 11'))

    def test_uma_saude_ativa_e_tipo_fora_do_check_recusado(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            VersaoTermo.objects.create(tipo='SAUDE', titulo='Dup', conteudo='x', versao='9',
                                       vigente_desde=timezone.localdate(), ativa=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            VersaoTermo.objects.create(tipo='OUTRO', titulo='X', conteudo='x', versao='1',
                                       vigente_desde=timezone.localdate(), ativa=False)

    def test_aceite_de_saude_com_prova_e_idempotente(self):
        cliente = criar_cliente()
        termo = VersaoTermo.saude_vigente()
        req = RequestFactory().post('/x/', REMOTE_ADDR='200.10.20.30', HTTP_USER_AGENT='Tablet/1.0')
        with override_settings(CLIENT_IP_HEADER=''):
            aceite = AceiteTermo.registrar(cliente, termo, req)
        self.assertEqual((aceite.ip, aceite.user_agent), ('200.10.20.30', 'Tablet/1.0'))
        self.assertEqual(aceite.conteudo_sha256, hashlib.sha256(TEXTO_CONSENTIMENTO_SAUDE.encode()).hexdigest())
        self.assertEqual(AceiteTermo.registrar(cliente, termo).pk, aceite.pk)


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


@unittest.skipUnless(connection.vendor == 'postgresql', 'trigger so no Postgres (0046)')
class ProvaAceiteTriggerPgTests(TestCase):
    """O guard de save() nao pega QuerySet.update()/SQL: o banco segura."""

    def setUp(self):
        prof = criar_profissional()
        self.cliente = criar_cliente()
        self.atendimento = criar_atendimento(self.cliente, prof, criar_procedimento(profissional=prof))
        self.termo = _termo(conteudo='Texto aceito')
        req = RequestFactory().post('/x/', REMOTE_ADDR='200.10.20.30', HTTP_USER_AGENT='A')
        self.aceite = AceiteTermo.registrar(self.cliente, self.termo, req, atendimento=self.atendimento)

    def _bloqueado(self, fn):
        with self.assertRaises(DatabaseError), transaction.atomic():
            fn()

    def test_aceite_nao_muda_nem_some(self):
        qs = AceiteTermo.objects.filter(pk=self.aceite.pk)
        self._bloqueado(lambda: qs.update(ip='1.1.1.1'))
        self._bloqueado(lambda: qs.update(conteudo_sha256='0' * 64))
        self._bloqueado(lambda: qs.update(user_agent='forjado'))
        outro = criar_atendimento(self.cliente, self.atendimento.profissional, self.atendimento.procedimento)
        self._bloqueado(lambda: qs.update(atendimento=outro))
        self._bloqueado(lambda: qs.delete())
        self.aceite.refresh_from_db()
        self.assertEqual((self.aceite.ip, self.aceite.user_agent), ('200.10.20.30', 'A'))

    def test_save_sem_mudanca_e_set_null_do_atendimento_passam(self):
        self.aceite.save()  # mesma linha: nada distinto
        AceiteTermo.objects.filter(pk=self.aceite.pk).update(atendimento=None)  # SET_NULL da FK
        self.aceite.refresh_from_db()
        self.assertIsNone(self.aceite.atendimento_id)

    def test_aceite_de_saude_tambem_imutavel_no_banco(self):
        """0047 nao recria as tabelas: os triggers da 0046 seguem valendo p/ SAUDE."""
        saude = VersaoTermo.saude_vigente()
        aceite = AceiteTermo.registrar(self.cliente, saude, atendimento=self.atendimento)
        self._bloqueado(lambda: AceiteTermo.objects.filter(pk=aceite.pk).update(ip='1.1.1.1'))
        self._bloqueado(lambda: AceiteTermo.objects.filter(pk=aceite.pk).delete())
        self._bloqueado(lambda: VersaoTermo.objects.filter(pk=saude.pk).update(conteudo='Outro texto'))
        with connection.cursor() as cur:
            cur.execute(
                "SELECT tgname FROM pg_trigger WHERE tgname IN "
                "('trg_aceite_termo_imutavel', 'trg_versao_termo_imutavel') AND NOT tgisinternal"
            )
            self.assertEqual(len(cur.fetchall()), 2)

    def test_versao_aceita_congelada_no_banco_mas_desativavel(self):
        qs = VersaoTermo.objects.filter(pk=self.termo.pk)
        self._bloqueado(lambda: qs.update(conteudo='Texto trocado'))
        self._bloqueado(lambda: qs.update(versao='9.9'))
        qs.update(ativa=False, vigente_desde=timezone.localdate())
        self.assertFalse(VersaoTermo.objects.get(pk=self.termo.pk).ativa)
        # versao sem aceite continua editavel
        rascunho = _termo(tipo='PROCEDIMENTO', procedimento=criar_procedimento(nome='Peeling'), conteudo='x')
        VersaoTermo.objects.filter(pk=rascunho.pk).update(conteudo='y')
        self.assertEqual(VersaoTermo.objects.get(pk=rascunho.pk).conteudo, 'y')
