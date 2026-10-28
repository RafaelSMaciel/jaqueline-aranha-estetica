"""Cifragem em repouso (utils/cifragem + db/campos): prontuario, fichas, anotacoes, token OAuth.

Um dump/backup do banco nao pode mostrar dado de saude (LGPD art. 11/46): aqui se confere o que
fica GRAVADO (SQL cru) e que a aplicacao segue vendo o texto claro — sem recifrar o que nao mudou
(Fernet e aleatorio: recifrar faria a trilha registrar alteracao fantasma).
"""
import io
import json

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings

from aranha_estetica import checks as aranha_checks
from aranha_estetica.db.campos import JsonCifrado, TextoCifrado
from aranha_estetica.models import (
    AnotacaoSessao, FormularioAnamnese, LogAuditoria, Prontuario, ProntuarioVersao,
    RespostaAnamnese,
)
from aranha_estetica.utils import cifragem

from .factories import criar_atendimento, criar_cliente, criar_procedimento, criar_profissional

# chaves so destes testes
CHAVE_A = 'plevXTY9uYG0_F2LdG9ZPH33xmxs1iZwPBJlqeU0NS4='
CHAVE_B = 'JGFGYB4wrPjyaDUdeZgaO9w5C3rDAN1Fx8ZlMNlZDNg='


def _cru(tabela, coluna, pk):
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {coluna} FROM {tabela} WHERE id = %s', [pk])
        return cursor.fetchone()[0]


def _json_cru(tabela, coluna, pk):
    valor = _cru(tabela, coluna, pk)
    return json.loads(valor) if isinstance(valor, str) else valor


@override_settings(CHAVES_CIFRAGEM=[CHAVE_A])
class CifragemTests(SimpleTestCase):
    def test_texto_e_json_vao_e_voltam(self):
        cifrado = cifragem.cifrar('Alergia a dipirona')
        self.assertTrue(cifrado.startswith('cif1:'))
        self.assertNotIn('dipirona', cifrado)
        self.assertEqual(cifragem.decifrar(cifrado), 'Alergia a dipirona')
        self.assertNotEqual(cifragem.cifrar('Alergia a dipirona'), cifrado)   # aleatorio
        obj = cifragem.cifrar_json({'gestante': True, 'alergias': 'latex'})
        self.assertEqual(set(obj), {'_cifra'})
        self.assertEqual(cifragem.decifrar_json(obj), {'gestante': True, 'alergias': 'latex'})
        self.assertEqual(cifragem.decifrar('texto legado em claro'), 'texto legado em claro')

    def test_finalidades_nao_se_abrem(self):
        token = cifragem.cifrar('refresh-token-google', 'segredos')
        with self.assertRaises(cifragem.ErroCifragem):
            cifragem.decifrar(token, 'saude')
        self.assertEqual(cifragem.decifrar(token, 'segredos'), 'refresh-token-google')

    def test_rotacao_nova_na_frente_antiga_ainda_abre(self):
        velho = cifragem.cifrar('historico de asma')
        with override_settings(CHAVES_CIFRAGEM=[CHAVE_B, CHAVE_A]):
            self.assertEqual(cifragem.decifrar(velho), 'historico de asma')
            novo = cifragem.recifrar(velho)
        with override_settings(CHAVES_CIFRAGEM=[CHAVE_B]):
            self.assertEqual(cifragem.decifrar(novo), 'historico de asma')
            with self.assertRaises(cifragem.ErroCifragem):
                cifragem.decifrar(velho)

    @override_settings(CHAVES_CIFRAGEM=[], CIFRAGEM_EXIGE_CHAVE=True)
    def test_prod_sem_chave_recusa_e_o_check_barra(self):
        with self.assertRaises(ImproperlyConfigured):
            cifragem.cifrar('x')
        self.assertEqual([e.id for e in aranha_checks.check_chaves_cifragem()], ['aranha.E001'])

    @override_settings(CHAVES_CIFRAGEM=['curta-demais'], CIFRAGEM_EXIGE_CHAVE=True)
    def test_chave_fora_do_formato_reprova(self):
        self.assertEqual([e.id for e in aranha_checks.check_chaves_cifragem()], ['aranha.E001'])

    @override_settings(CIFRAGEM_EXIGE_CHAVE=True)
    def test_prod_com_chave_valida_passa(self):
        self.assertEqual(aranha_checks.check_chaves_cifragem(), [])

    @override_settings(CHAVES_CIFRAGEM=[])
    def test_dev_sem_chave_deriva_da_secret_key(self):
        self.assertEqual(cifragem.decifrar(cifragem.cifrar('ok')), 'ok')

    def test_decifrar_profundo_da_trilha(self):
        trilha = {'alergias': cifragem.cifrar('latex'), 'respostas': cifragem.cifrar_json({'fuma': False}),
                  'status': 'ok'}
        self.assertEqual(cifragem.decifrar_profundo(trilha),
                         {'alergias': 'latex', 'respostas': {'fuma': False}, 'status': 'ok'})
        with override_settings(CHAVES_CIFRAGEM=[CHAVE_B]):
            self.assertEqual(cifragem.decifrar_profundo(trilha)['alergias'], '[cifrado: chave indisponivel]')


@override_settings(CHAVES_CIFRAGEM=[CHAVE_A])
class CamposCifradosTests(TestCase):
    def setUp(self):
        self.cli = criar_cliente()

    def test_prontuario_gravado_cifrado_e_lido_em_claro(self):
        p = Prontuario.objects.create(cliente=self.cli, alergias='Dipirona', historico_saude='',
                                      respostas_extras={'gestante': 'nao'})
        self.assertTrue(_cru('prontuario', 'alergias', p.pk).startswith('cif1:'))
        self.assertEqual(_cru('prontuario', 'historico_saude', p.pk), '')          # vazio fica vazio
        self.assertEqual(set(_json_cru('prontuario', 'respostas', p.pk)), {'_cifra'})
        p = Prontuario.objects.get(pk=p.pk)
        self.assertEqual((p.alergias, p.respostas_extras), ('Dipirona', {'gestante': 'nao'}))
        self.assertEqual(
            Prontuario.objects.filter(pk=p.pk).values_list('alergias', flat=True).get(), 'Dipirona')
        self.assertTrue(Prontuario.objects.filter(Prontuario.Q_COM_CONTEUDO, pk=p.pk).exists())

    def test_vazio_fica_vazio_no_banco(self):
        p = Prontuario.objects.create(cliente=self.cli)
        self.assertEqual(_json_cru('prontuario', 'respostas', p.pk), {})
        self.assertFalse(Prontuario.objects.filter(Prontuario.Q_COM_CONTEUDO, pk=p.pk).exists())

    def test_save_sem_mudanca_nao_recifra(self):
        p = Prontuario.objects.create(cliente=self.cli, alergias='Latex', contraindicacoes='Gestante',
                                      respostas_extras={'fuma': False})
        antes = {c: _cru('prontuario', c, p.pk) for c in ('alergias', 'contraindicacoes', 'respostas')}
        p = Prontuario.objects.get(pk=p.pk)
        p.alergias = 'Latex'                       # mesmo conteudo, str nova (como o form faz)
        p.respostas_extras = {'fuma': False}       # dict novo igual
        p.contraindicacoes = 'Nenhuma'             # so este muda
        p.save()
        depois = {c: _cru('prontuario', c, p.pk) for c in ('alergias', 'contraindicacoes', 'respostas')}
        self.assertEqual(depois['alergias'], antes['alergias'])
        self.assertEqual(depois['respostas'], antes['respostas'])
        self.assertNotEqual(depois['contraindicacoes'], antes['contraindicacoes'])
        self.assertEqual(Prontuario.objects.get(pk=p.pk).contraindicacoes, 'Nenhuma')

    def test_mutacao_no_dict_lido_e_gravada(self):
        p = Prontuario.objects.create(cliente=self.cli, respostas_extras={'fuma': False})
        p = Prontuario.objects.get(pk=p.pk)
        p.respostas_extras['fuma'] = True
        p.save()
        self.assertEqual(Prontuario.objects.get(pk=p.pk).respostas_extras, {'fuma': True})

    def test_legado_em_claro_le_e_cifra_no_proximo_save(self):
        p = Prontuario.objects.create(cliente=self.cli)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE prontuario SET alergias = 'Legado' WHERE id = %s", [p.pk])
        p = Prontuario.objects.get(pk=p.pk)
        self.assertEqual(p.alergias, 'Legado')
        p.save()
        self.assertTrue(_cru('prontuario', 'alergias', p.pk).startswith('cif1:'))

    def test_versao_anotacao_e_ficha(self):
        p = Prontuario.objects.create(cliente=self.cli, alergias='Latex')
        versao = ProntuarioVersao.registrar(p, None)
        self.assertEqual(set(_json_cru('prontuario_versao', 'dados', versao.pk)), {'_cifra'})
        self.assertEqual(ProntuarioVersao.objects.get(pk=versao.pk).dados['alergias'], 'Latex')

        prof = criar_profissional()
        at = criar_atendimento(self.cli, prof, criar_procedimento(profissional=prof))
        nota = AnotacaoSessao.objects.create(atendimento=at, texto='Reacao leve na regiao')
        self.assertTrue(_cru('anotacao_sessao', 'texto', nota.pk).startswith('cif1:'))
        self.assertEqual(AnotacaoSessao.objects.get(pk=nota.pk).texto, 'Reacao leve na regiao')

        form = FormularioAnamnese.objects.create(nome='Ficha', tipo='ANAMNESE', escopo='GLOBAL', schema_json=[])
        ficha = RespostaAnamnese.objects.create(formulario=form, cliente=self.cli, respostas_json={'diabetes': True})
        vazia = RespostaAnamnese.objects.create(formulario=form, cliente=self.cli)
        self.assertEqual(set(_json_cru('resposta_formulario', 'respostas', ficha.pk)), {'_cifra'})
        self.assertEqual(_json_cru('resposta_formulario', 'respostas', vazia.pk), {})
        self.assertEqual(RespostaAnamnese.objects.get(pk=ficha.pk).respostas_json, {'diabetes': True})

    def test_rotacionar_chave(self):
        p = Prontuario.objects.create(cliente=self.cli, alergias='Latex', respostas_extras={'fuma': True})
        velho = _cru('prontuario', 'alergias', p.pk)
        with override_settings(CHAVES_CIFRAGEM=[CHAVE_B, CHAVE_A]):
            call_command('rotacionar_chave_cifragem', stdout=io.StringIO())
        self.assertNotEqual(_cru('prontuario', 'alergias', p.pk), velho)
        with override_settings(CHAVES_CIFRAGEM=[CHAVE_B]):
            p = Prontuario.objects.get(pk=p.pk)
            self.assertEqual((p.alergias, p.respostas_extras), ('Latex', {'fuma': True}))
        self.assertTrue(LogAuditoria.objects.filter(acao='Rotacionou a chave de cifragem em repouso').exists())


class InventarioCifradoTests(SimpleTestCase):
    def test_rotacao_e_migration_cobrem_todo_campo_cifrado(self):
        """Campo cifrado novo tem de entrar na rotacao (senao a chave antiga nunca sai)."""
        from django.apps import apps

        from aranha_estetica.management.commands import rotacionar_chave_cifragem as rotacao

        textos, jsons = set(), set()
        for model in apps.get_app_config('aranha_estetica').get_models():
            for field in model._meta.concrete_fields:
                if isinstance(field, TextoCifrado):
                    textos.add((model._meta.db_table, field.column, field.finalidade))
                elif isinstance(field, JsonCifrado):
                    jsons.add((model._meta.db_table, field.column, field.finalidade))
        self.assertEqual(textos, set(rotacao.TEXTOS))
        self.assertEqual(jsons, set(rotacao.JSONS))
