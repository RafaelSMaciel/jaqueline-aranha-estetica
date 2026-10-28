"""Restauracao de backup: o esquecimento feito depois do backup e reaplicado
(management/commands/reaplicar_esquecimentos) e os dumps nunca entram no git/imagem."""
import io
import json
import tempfile
from fnmatch import fnmatch
from pathlib import Path

from unittest import skipUnless

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase

from aranha_estetica.models import Cliente, LogAuditoria
from aranha_estetica.services.lgpd import LgpdService

from .factories import criar_cliente


@skipUnless(connection.vendor == 'postgresql', 'sp_anonimizar_cliente so no Postgres')
class ReaplicarEsquecimentosTests(TestCase):
    def test_lista_antes_e_reaplica_no_banco_restaurado(self):
        esquecida = criar_cliente('Ana Esquecida', telefone='11988880001')
        outra = criar_cliente('Bia Fica', telefone='11988880002')
        LgpdService.esquecer_cliente(esquecida)

        saida = io.StringIO()
        call_command('reaplicar_esquecimentos', '--listar', '--desde', '2000-01-01', stdout=saida)
        lista = json.loads(saida.getvalue())
        self.assertEqual(lista['clientes'], [esquecida.pk])

        # "restaura o backup": a cliente volta identificada (como no dump de antes do pedido)
        Cliente.all_objects.filter(pk=esquecida.pk).update(nome='Ana Esquecida', telefone='11988880001',
                                                           deletado_em=None, ativo=True)
        with tempfile.TemporaryDirectory() as tmp:
            arquivo = Path(tmp) / 'esquecidos.json'
            arquivo.write_text(json.dumps({**lista, 'clientes': lista['clientes'] + [999999]}), encoding='utf-8')
            saida = io.StringIO()
            call_command('reaplicar_esquecimentos', str(arquivo), stdout=saida)
        self.assertIn('1 anonimizados de novo', saida.getvalue())
        self.assertIn('1 nao existem neste backup', saida.getvalue())
        esquecida = Cliente.all_objects.get(pk=esquecida.pk)
        self.assertTrue(esquecida.nome.startswith('[ANONIMIZADO-'))
        self.assertIsNone(esquecida.telefone)
        self.assertEqual(Cliente.objects.get(pk=outra.pk).nome, 'Bia Fica')
        self.assertTrue(LogAuditoria.objects.filter(
            acao='Reaplicou esquecimentos apos restauracao de backup').exists())


class DumpsForaDoRepositorioTests(SimpleTestCase):
    DUMPS = ('*.dump', '*.dump.age', '*.backup', '*.sql.gz')

    def test_gitignore_e_dockerignore_bloqueiam_dump(self):
        # .gitignore: padrao sem barra vale em qualquer pasta
        linhas = set((Path(settings.BASE_DIR) / '.gitignore').read_text(encoding='utf-8').split())
        for padrao in self.DUMPS:
            with self.subTest(arquivo='.gitignore', padrao=padrao):
                self.assertIn(padrao, linhas)
        # .dockerignore: sem '**/' o padrao so casa na raiz do contexto do build
        globais = [linha for linha in (Path(settings.BASE_DIR) / '.dockerignore').read_text(encoding='utf-8').split()
                   if linha.startswith('**/')]
        for padrao in self.DUMPS:
            with self.subTest(arquivo='.dockerignore', padrao=padrao):
                exemplo = 'aranha_estetica/sub/backup' + padrao[1:]
                self.assertTrue(any(fnmatch(exemplo, glob) for glob in globais), exemplo)
