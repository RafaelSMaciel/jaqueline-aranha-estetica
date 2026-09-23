"""Wave 3 (models): historico do prontuario (ProntuarioVersao), autoria da
trilha de auditoria (LogAuditoria.usuario_nome), Prontuario.Q_COM_CONTEUDO e
login insensivel a caixa (UsuarioManager.get_by_natural_key)."""
import unittest

from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from django.test import RequestFactory, TestCase

from aranha_estetica.models import LogAuditoria, Prontuario, ProntuarioVersao, Usuario
from aranha_estetica.utils.audit import registrar_log

from .factories import criar_cliente

SENHA = 'Senha-Forte-2026!x'


def _usuario(email='dra@clinica.com', nome='Dra. Clara', **kw):
    kw.setdefault('papel', Usuario.PAPEL_PROFISSIONAL)
    return Usuario.objects.create_user(email, SENHA, nome=nome, **kw)


class ProntuarioVersaoTests(TestCase):
    def setUp(self):
        self.autora = _usuario()
        self.pront = Prontuario.objects.create(cliente=criar_cliente())

    def _editar(self, usuario=None, **campos):
        """Espelha prontuario_salvar: registrar() antes do save."""
        with transaction.atomic():
            pront = Prontuario.objects.select_for_update().get(pk=self.pront.pk)
            for campo, valor in campos.items():
                setattr(pront, campo, valor)
            versao = ProntuarioVersao.registrar(pront, usuario or self.autora)
            pront.save()
        return versao

    def test_primeiro_preenchimento_nao_gera_versao_vazia(self):
        self.assertIsNone(self._editar(alergias='Dipirona'))
        self.assertFalse(ProntuarioVersao.objects.exists())

    def test_edicao_guarda_o_valor_anterior_com_autor(self):
        self._editar(alergias='Dipirona', respostas_extras={'gestante': False})
        versao = self._editar(alergias='')  # apagou a alergia
        self.assertIsNotNone(versao)
        versao.refresh_from_db()
        self.assertEqual(versao.dados['alergias'], 'Dipirona')
        self.assertEqual(versao.dados['respostas_extras'], {'gestante': False})
        self.assertEqual(set(versao.dados), {*Prontuario.CAMPOS_CLINICOS, 'respostas_extras'})
        self.assertEqual(versao.autor, self.autora)
        self.assertEqual(versao.autor_nome, 'Dra. Clara')
        self.assertEqual(list(self.pront.versoes.all()), [versao])
        self.assertTrue(str(versao).startswith(f'Versao do prontuario {self.pront.pk} ('))
        # estado apagado tambem vira versao: o historico nao tem buraco
        self.assertIsNotNone(self._editar(alergias='Latex'))
        self.assertEqual(self.pront.versoes.count(), 2)

    def test_prontuario_sem_pk_ou_usuario_anonimo(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertIsNone(ProntuarioVersao.registrar(Prontuario(cliente=criar_cliente(telefone='11977776666')),
                                                     self.autora))
        self._editar(alergias='Dipirona')
        versao = self._editar(AnonymousUser(), alergias='Nenhuma')
        self.assertIsNone(versao.autor)
        self.assertEqual(versao.autor_nome, '')

    def test_append_only(self):
        self._editar(alergias='Dipirona')
        versao = self._editar(alergias='Latex')
        versao.dados = {'alergias': 'forjado'}
        with self.assertRaises(ValidationError):
            versao.save()
        with self.assertRaises(ValidationError):
            versao.delete()
        with self.assertRaises(ValidationError):
            ProntuarioVersao.objects.filter(pk=versao.pk).delete()
        self.assertEqual(ProntuarioVersao.objects.get(pk=versao.pk).dados['alergias'], 'Dipirona')

    def test_excluir_autor_mantem_autor_nome(self):
        self._editar(alergias='Dipirona')
        versao = self._editar(alergias='Latex')
        self.autora.delete()
        versao = ProntuarioVersao.objects.get(pk=versao.pk)
        self.assertIsNone(versao.autor_id)
        self.assertEqual(versao.autor_nome, 'Dra. Clara')

    @unittest.skipUnless(connection.vendor == 'postgresql', 'trigger so no Postgres (0046)')
    def test_trigger_pg_bloqueia_update_e_delete(self):
        self._editar(alergias='Dipirona')
        versao = self._editar(alergias='Latex')
        qs = ProntuarioVersao.objects.filter(pk=versao.pk)
        with self.assertRaises(DatabaseError), transaction.atomic():
            qs.update(dados={'alergias': 'forjado'})
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cur:
            cur.execute('DELETE FROM prontuario_versao WHERE id = %s', [versao.pk])
        qs.update(autor=None)  # SET_NULL da FK continua permitido
        self.assertEqual(qs.get().dados['alergias'], 'Dipirona')


class ProntuarioComConteudoTests(TestCase):
    def test_registro_vazio_nao_conta(self):
        vazio_none = Prontuario.objects.create(cliente=criar_cliente(telefone='11911110001'))
        vazio_str = Prontuario.objects.create(cliente=criar_cliente(telefone='11911110002'),
                                              alergias='', observacoes_gerais='')
        texto = Prontuario.objects.create(cliente=criar_cliente(telefone='11911110003'), medicamentos_uso='Insulina')
        extras = Prontuario.objects.create(cliente=criar_cliente(telefone='11911110004'),
                                           respostas_extras={'fumante': True})
        com_conteudo = set(Prontuario.objects.filter(Prontuario.Q_COM_CONTEUDO).values_list('pk', flat=True))
        self.assertEqual(com_conteudo, {texto.pk, extras.pk})
        self.assertNotIn(vazio_none.pk, com_conteudo)
        self.assertNotIn(vazio_str.pk, com_conteudo)


class LogAuditoriaAutoriaTests(TestCase):
    def test_snapshot_do_autor_sobrevive_a_exclusao_do_usuario(self):
        admin = _usuario('dona@clinica.com', 'Dona Jaqueline', papel=Usuario.PAPEL_ADMIN)
        registrar_log(admin, 'Acessou prontuario', 'prontuario', 1)
        log = LogAuditoria.objects.get()
        self.assertEqual(log.usuario_nome, 'Dona Jaqueline <dona@clinica.com>')
        admin.nome = 'Outro Nome'
        admin.save()
        admin.delete()
        log.refresh_from_db()
        self.assertIsNone(log.usuario_id)
        self.assertEqual(log.usuario_nome, 'Dona Jaqueline <dona@clinica.com>')

    def test_acao_do_sistema_fica_sem_autor(self):
        registrar_log(None, 'Purga automatica', 'cliente', None)
        self.assertEqual(LogAuditoria.objects.get().usuario_nome, '')


class LoginCaixaInsensivelTests(TestCase):
    def _auth(self, username):
        request = RequestFactory().post('/admin-login/')
        return authenticate(request, username=username, password=SENHA)

    def test_login_com_outra_caixa(self):
        u = _usuario('Dona@clinica.com', 'Dona')
        self.assertEqual(Usuario.objects.get_by_natural_key('DONA@Clinica.COM'), u)
        self.assertEqual(self._auth('dona@CLINICA.com'), u)
        self.assertIsNone(self._auth('outra@clinica.com'))

    def test_legado_com_duplicata_por_caixa_nao_quebra(self):
        a = _usuario('a@x.com', 'A')
        b = _usuario('A@x.com', 'B')
        self.assertEqual(self._auth('a@x.com'), a)  # exato primeiro
        self.assertEqual(self._auth('A@x.com'), b)
        with self.assertRaises(Usuario.DoesNotExist):
            Usuario.objects.get_by_natural_key('a@X.com')  # ambiguo: falha limpo
        self.assertIsNone(self._auth('a@X.com'))
