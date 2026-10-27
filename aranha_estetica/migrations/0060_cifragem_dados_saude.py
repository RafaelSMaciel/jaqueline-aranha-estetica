# Dado de saude e segredo OAuth cifrados em repouso (LGPD art. 11/46; utils/cifragem)
#
# Campos (TrocarClasseDoCampo: estado + so COMMENT ON COLUMN; o tipo nao muda — text/jsonb — e o
# ALTER COLUMN TYPE do AlterField falharia nas colunas usadas pela 0057):
#   prontuario.{alergias,contraindicacoes,historico_saude,medicamentos_uso,observacoes_gerais}
#   prontuario.respostas, prontuario_versao.dados, anotacao_sessao.texto,
#   resposta_formulario.respostas (ficha de anamnese) -> finalidade 'saude'
#   profissional.gcal_token_renovacao                 -> finalidade 'segredos'
# Texto vira 'cif1:<token Fernet>'; JSON vira {"_cifra": "<token>"} (segue objeto: os CHECKs
# jsonb_typeof = 'object' e os `<> '{}'` / `<> ''` do SQL da 0057 continuam valendo). Vazio fica
# vazio.
#
# Dados: cifra o que ja existe, inclusive os valores de saude dentro de historico_alteracao
# (tabela = 'prontuario'), com os triggers de usuario das tabelas desligados durante o UPDATE
# (no PG): sem isso a trilha gravaria o texto claro em dados_antes, as versoes/anotacoes
# imutaveis recusariam e atualizado_em mudaria. Idempotente (pula o que ja tem o prefixo).
# Exige a chave: prod sem CHAVES_CIFRAGEM aborta aqui (e o check aranha.E001 avisa antes).
# Reverso: decifra tudo de volta (mesma chave).
import json

import aranha_estetica.db.campos
from django.db import migrations

from aranha_estetica.db.operacoes import TrocarClasseDoCampo
from aranha_estetica.utils import cifragem

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
    ('prontuario', 'respostas'),
    ('prontuario_versao', 'dados'),
    ('resposta_formulario', 'respostas'),
)
TABELAS = ('anotacao_sessao', 'historico_alteracao', 'profissional', 'prontuario', 'prontuario_versao',
           'resposta_formulario')
TEXTOS_TRILHA = ('alergias', 'contraindicacoes', 'historico_saude', 'medicamentos_uso', 'observacoes_gerais')


def _cifrar_texto(valor, finalidade):
    return cifragem.cifrar(valor, finalidade) if valor and not cifragem.esta_cifrado(valor) else valor


def _decifrar_texto(valor, finalidade):
    return cifragem.decifrar(valor, finalidade) if cifragem.esta_cifrado(valor) else valor


def _cifrar_json(valor):
    if valor in ({}, None) or cifragem.json_cifrado(valor):
        return valor
    return cifragem.cifrar_json(valor, 'saude')


def _decifrar_json(valor):
    return cifragem.decifrar_json(valor, 'saude') if cifragem.json_cifrado(valor) else valor


def _json(bruto):
    return json.loads(bruto) if isinstance(bruto, str) else bruto


def _converter(schema_editor, texto, objeto):
    conexao = schema_editor.connection
    pg = conexao.vendor == 'postgresql'
    jsonb = '::jsonb' if pg else ''
    with conexao.cursor() as cursor:
        if pg:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')       # sem eventos pendentes p/ o ALTER
            for tabela in TABELAS:
                cursor.execute(f'ALTER TABLE {tabela} DISABLE TRIGGER USER')
        for tabela, coluna, finalidade in TEXTOS:
            cursor.execute(f"SELECT id, {coluna} FROM {tabela} WHERE {coluna} <> ''")
            for pk, valor in cursor.fetchall():
                novo = texto(valor, finalidade)
                if novo != valor:
                    cursor.execute(f'UPDATE {tabela} SET {coluna} = %s WHERE id = %s', [novo, pk])
        for tabela, coluna in JSONS:
            cursor.execute(f'SELECT id, {coluna} FROM {tabela}')
            for pk, bruto in cursor.fetchall():
                valor = _json(bruto)
                novo = objeto(valor)
                if novo != valor:
                    cursor.execute(f'UPDATE {tabela} SET {coluna} = %s{jsonb} WHERE id = %s',
                                   [json.dumps(novo), pk])
        cursor.execute("SELECT id, dados_antes, dados_depois FROM historico_alteracao WHERE tabela = 'prontuario'")
        for pk, antes, depois in cursor.fetchall():
            fotos = []
            for bruto in (antes, depois):
                foto = _json(bruto)
                if isinstance(foto, dict):
                    foto = dict(foto)
                    for coluna in TEXTOS_TRILHA:
                        if isinstance(foto.get(coluna), str):
                            foto[coluna] = texto(foto[coluna], 'saude')
                    if 'respostas' in foto:
                        foto['respostas'] = objeto(foto['respostas'])
                fotos.append(foto)
            if fotos != [_json(antes), _json(depois)]:
                cursor.execute(
                    f'UPDATE historico_alteracao SET dados_antes = %s{jsonb}, dados_depois = %s{jsonb} WHERE id = %s',
                    [None if f is None else json.dumps(f) for f in fotos] + [pk],
                )
        if pg:
            for tabela in TABELAS:
                cursor.execute(f'ALTER TABLE {tabela} ENABLE TRIGGER USER')


def cifrar(apps, schema_editor):
    _converter(schema_editor, _cifrar_texto, _cifrar_json)


def decifrar(apps, schema_editor):
    _converter(schema_editor, _decifrar_texto, _decifrar_json)


class Migration(migrations.Migration):

    dependencies = [
        ('aranha_estetica', '0059_trilha_append_only'),
    ]

    operations = [
        TrocarClasseDoCampo(
            model_name='anotacaosessao',
            name='texto',
            field=aranha_estetica.db.campos.TextoCifrado(db_comment='Cifrado (cif1:; utils/cifragem).'),
        ),
        TrocarClasseDoCampo(
            model_name='profissional',
            name='gcal_refresh_token',
            field=aranha_estetica.db.campos.TextoCifrado(blank=True, db_column='gcal_token_renovacao', db_comment='Refresh token OAuth do Google Calendar - segredo reversivel (nao da p/ hashear): cifrado (cif1:, finalidade segredos; utils/cifragem).', db_default='', default='', finalidade='segredos'),
        ),
        TrocarClasseDoCampo(
            model_name='prontuario',
            name='alergias',
            field=aranha_estetica.db.campos.TextoCifrado(blank=True, db_comment='Cifrado (cif1:; utils/cifragem).', db_default='', default=''),
        ),
        TrocarClasseDoCampo(
            model_name='prontuario',
            name='contraindicacoes',
            field=aranha_estetica.db.campos.TextoCifrado(blank=True, db_comment='Cifrado (cif1:; utils/cifragem).', db_default='', default=''),
        ),
        TrocarClasseDoCampo(
            model_name='prontuario',
            name='historico_saude',
            field=aranha_estetica.db.campos.TextoCifrado(blank=True, db_comment='Cifrado (cif1:; utils/cifragem).', db_default='', default=''),
        ),
        TrocarClasseDoCampo(
            model_name='prontuario',
            name='medicamentos_uso',
            field=aranha_estetica.db.campos.TextoCifrado(blank=True, db_comment='Cifrado (cif1:; utils/cifragem).', db_default='', default=''),
        ),
        TrocarClasseDoCampo(
            model_name='prontuario',
            name='observacoes_gerais',
            field=aranha_estetica.db.campos.TextoCifrado(blank=True, db_comment='Cifrado (cif1:; utils/cifragem).', db_default='', default=''),
        ),
        TrocarClasseDoCampo(
            model_name='prontuario',
            name='respostas_extras',
            field=aranha_estetica.db.campos.JsonCifrado(blank=True, db_column='respostas', db_comment='Respostas do questionario do prontuario {key: valor}; keys = formulario.perguntas do formulario tipo PRONTUARIO ativo. Cifrado: {"_cifra": token} ({} = vazio; utils/cifragem).', db_default={}, default=dict),
        ),
        TrocarClasseDoCampo(
            model_name='prontuarioversao',
            name='dados',
            field=aranha_estetica.db.campos.JsonCifrado(db_comment='Chaves = atributos Python (CAMPOS_CLINICOS + respostas_extras). E registro historico: nunca renomear as chaves. Cifrado: {"_cifra": token} (utils/cifragem).', db_default={}, default=dict),
        ),
        TrocarClasseDoCampo(
            model_name='respostaanamnese',
            name='respostas_json',
            field=aranha_estetica.db.campos.JsonCifrado(db_column='respostas', db_comment='Respostas {key: valor}. Cifrado: {"_cifra": token} ({} = vazio; utils/cifragem).', db_default={}, default=dict),
        ),
        migrations.RunPython(cifrar, decifrar),
    ]
