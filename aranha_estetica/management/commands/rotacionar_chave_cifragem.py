"""Recifra todo dado cifrado em repouso com a 1a chave de CHAVES_CIFRAGEM.

Uso (rotacao de chave):
    1. CHAVES_CIFRAGEM=<nova>,<antiga> e redeploy (a nova cifra, as duas decifram)
    2. python manage.py rotacionar_chave_cifragem
    3. CHAVES_CIFRAGEM=<nova> e redeploy (a antiga pode ser descartada... depois do proximo
       backup: backup antigo so abre com a chave antiga — guarde-a enquanto o backup existir)

Uma transacao so; no Postgres os triggers de usuario das tabelas ficam desligados durante o
UPDATE (as versoes/anotacoes sao imutaveis e a trilha registraria a troca de texto cifrado como
alteracao). Inclui os valores de saude dentro de historico_alteracao (tabela = 'prontuario').
Auditado em log_auditoria. --dry-run so conta.
"""
import json

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from aranha_estetica.utils import cifragem
from aranha_estetica.utils.audit import registrar_log

# (tabela, coluna, finalidade) — tests/test_cifragem confere contra os campos dos models
TEXTOS = (
    ('prontuario', 'alergias', 'saude'),
    ('prontuario', 'contraindicacoes', 'saude'),
    ('prontuario', 'historico_saude', 'saude'),
    ('prontuario', 'medicamentos_uso', 'saude'),
    ('prontuario', 'observacoes_gerais', 'saude'),
    ('anotacao_sessao', 'texto', 'saude'),
    ('profissional', 'gcal_token_renovacao', 'segredos'),
)
JSONS = (
    ('prontuario', 'respostas', 'saude'),
    ('prontuario_versao', 'dados', 'saude'),
    ('resposta_formulario', 'respostas', 'saude'),
)
# colunas de prontuario cujo valor aparece em historico_alteracao.dados_antes/dados_depois
TRILHA = {'prontuario': ('alergias', 'contraindicacoes', 'historico_saude', 'medicamentos_uso',
                         'observacoes_gerais', 'respostas')}


def _json(bruto):
    return json.loads(bruto) if isinstance(bruto, str) else bruto


class Command(BaseCommand):
    help = 'Recifra o dado cifrado em repouso com a 1a chave de CHAVES_CIFRAGEM (rotacao).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='So conta, nao grava.')

    def handle(self, *args, **options):
        seco = options['dry_run']
        qtd_chaves = len(cifragem.chaves_mestras())
        if qtd_chaves < 2:
            self.stdout.write(self.style.WARNING(
                'CHAVES_CIFRAGEM tem 1 chave: recifra com ela mesma (rotacao = nova na frente).'))
        tabelas = sorted({t for t, *_ in TEXTOS + JSONS} | {'historico_alteracao'})
        pg = connection.vendor == 'postgresql'
        jsonb = '::jsonb' if pg else ''
        contagem = {}
        with transaction.atomic(), connection.cursor() as cursor:
            if pg and not seco:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                for tabela in tabelas:
                    cursor.execute(f'ALTER TABLE {tabela} DISABLE TRIGGER USER')
            for tabela, coluna, finalidade in TEXTOS:
                cursor.execute(f"SELECT id, {coluna} FROM {tabela} WHERE {coluna} LIKE %s", [cifragem.PREFIXO + '%'])
                linhas = cursor.fetchall()
                contagem[f'{tabela}.{coluna}'] = len(linhas)
                if not seco:
                    for pk, valor in linhas:
                        cursor.execute(f'UPDATE {tabela} SET {coluna} = %s WHERE id = %s',
                                       [cifragem.recifrar(valor, finalidade), pk])
            for tabela, coluna, finalidade in JSONS:
                cursor.execute(f'SELECT id, {coluna} FROM {tabela}')
                linhas = [(pk, _json(b)) for pk, b in cursor.fetchall()]
                linhas = [(pk, v) for pk, v in linhas if cifragem.json_cifrado(v)]
                contagem[f'{tabela}.{coluna}'] = len(linhas)
                if not seco:
                    for pk, valor in linhas:
                        cursor.execute(f'UPDATE {tabela} SET {coluna} = %s{jsonb} WHERE id = %s',
                                       [json.dumps(cifragem.recifrar(valor, finalidade)), pk])
            n_trilha = 0
            for tabela_trilha, colunas in TRILHA.items():
                cursor.execute('SELECT id, dados_antes, dados_depois FROM historico_alteracao WHERE tabela = %s',
                               [tabela_trilha])
                for pk, antes, depois in cursor.fetchall():
                    fotos = [_json(antes), _json(depois)]
                    novas = []
                    for foto in fotos:
                        if isinstance(foto, dict):
                            foto = {k: (cifragem.recifrar(v, 'saude') if k in colunas else v) for k, v in foto.items()}
                        novas.append(foto)
                    if novas != fotos:
                        n_trilha += 1
                        if not seco:
                            cursor.execute(
                                f'UPDATE historico_alteracao SET dados_antes = %s{jsonb}, dados_depois = %s{jsonb} '
                                'WHERE id = %s',
                                [None if f is None else json.dumps(f) for f in novas] + [pk],
                            )
            contagem['historico_alteracao'] = n_trilha
            if pg and not seco:
                for tabela in tabelas:
                    cursor.execute(f'ALTER TABLE {tabela} ENABLE TRIGGER USER')
            if not seco:
                registrar_log(None, 'Rotacionou a chave de cifragem em repouso', 'cifragem', None,
                              detalhes={'qtd_chaves': qtd_chaves, 'recifrados': contagem})
        total = sum(contagem.values())
        verbo = 'a recifrar' if seco else 'recifrados'
        self.stdout.write(self.style.SUCCESS(f'{total} valores {verbo}: {contagem}'))
