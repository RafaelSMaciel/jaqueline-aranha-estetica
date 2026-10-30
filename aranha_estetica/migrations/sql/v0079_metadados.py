"""0079 — metadados de linha em ingles (padrao-sql §1.2): criado_em -> created_at, atualizado_em ->
updated_at, deletado_em -> deleted_at, usuario_autor_id -> created_by_id, usuario_autor_nome ->
created_by_name. O atributo Python nao muda (db_column).

CONGELADO: importado so pela 0079_metadados_em_ingles (e pelos testes que conferem a versao
vigente). Mudanca = modulo novo + migration nova com CREATE OR REPLACE.

O RENAME COLUMN e so catalogo: indice, CHECK, FK, EXCLUDE, view e funcao BEGIN ATOMIC guardam a
coluna por attnum e seguem sozinhos (fn_nps_periodo, fn_saldo_comissao, fn_clientes_para_anonimizar,
vw_*). Corpo PL/pgSQL e texto: as funcoes e procedures abaixo sao as versoes vigentes passadas por
``em_ingles``, que troca so o nome inteiro da coluna (``\\b``: nao mexe em ``v_criado_em`` nem em
``fn_definir_atualizado_em``). O reverso recria os textos de origem.

fn_definir_atualizado_em vira fn_set_updated_at (e os triggers tg_<t>_atualizado_em viram
tg_<t>_updated_at). Tabela nova com updated_at: ``tg_updated_at(tabela)`` na mesma migration.
"""
import importlib
import re

from . import v0056_triggers as v0056
from . import v0059_trilha_append_only as v0059
from . import v0072_links as v0072
from . import v0077_retencao as v0077

COLUNAS = {
    'criado_em': 'created_at',
    'atualizado_em': 'updated_at',
    'deletado_em': 'deleted_at',
    'usuario_autor_id': 'created_by_id',
    'usuario_autor_nome': 'created_by_name',
}
_COLUNA = re.compile(r'\b(' + '|'.join(COLUNAS) + r')\b')


def em_ingles(sql):
    return _COLUNA.sub(lambda m: COLUNAS[m.group(1)], sql)


def _sql_da_0050():
    return importlib.import_module('aranha_estetica.migrations.0050_consentimento').SQL_FUNCAO


# funcao/procedure -> texto vigente ANTES da 0079 (o reverso volta a ele)
ORIGEM = {
    'fn_consentimento_imutavel': _sql_da_0050(),
    'fn_log_auditoria_imutavel': v0059.FN_LOG_AUDITORIA_IMUTAVEL,
    'fn_historico_alteracao_imutavel': v0059.FN_HISTORICO_ALTERACAO_IMUTAVEL,
    'fn_link_acesso_imutavel': v0072.FN_LINK_ACESSO_IMUTAVEL,
    'fn_prontuario_versao_imutavel': v0077.FN_PRONTUARIO_VERSAO_IMUTAVEL,
    'fn_anotacao_sessao_imutavel': v0077.FN_ANOTACAO_SESSAO_IMUTAVEL,
    'fn_aceite_termo_imutavel': v0077.FN_ACEITE_TERMO_IMUTAVEL,
    'fn_registrar_historico': v0077.FN_REGISTRAR_HISTORICO,
    'sp_anonimizar_cliente': v0077.SP_ANONIMIZAR_CLIENTE,
    'sp_aplicar_retencao_lgpd': v0077.SP_APLICAR_RETENCAO_LGPD,
    'sp_purgar_dados_expirados': v0077.SP_PURGAR_DADOS_EXPIRADOS,
}

FN_CONSENTIMENTO_IMUTAVEL = em_ingles(ORIGEM['fn_consentimento_imutavel'])
FN_LOG_AUDITORIA_IMUTAVEL = em_ingles(ORIGEM['fn_log_auditoria_imutavel'])
FN_HISTORICO_ALTERACAO_IMUTAVEL = em_ingles(ORIGEM['fn_historico_alteracao_imutavel'])
FN_LINK_ACESSO_IMUTAVEL = em_ingles(ORIGEM['fn_link_acesso_imutavel'])
FN_PRONTUARIO_VERSAO_IMUTAVEL = em_ingles(ORIGEM['fn_prontuario_versao_imutavel'])
FN_ANOTACAO_SESSAO_IMUTAVEL = em_ingles(ORIGEM['fn_anotacao_sessao_imutavel'])
FN_ACEITE_TERMO_IMUTAVEL = em_ingles(ORIGEM['fn_aceite_termo_imutavel'])
FN_REGISTRAR_HISTORICO = em_ingles(ORIGEM['fn_registrar_historico'])
SP_ANONIMIZAR_CLIENTE = em_ingles(ORIGEM['sp_anonimizar_cliente'])
SP_APLICAR_RETENCAO_LGPD = em_ingles(ORIGEM['sp_aplicar_retencao_lgpd'])
SP_PURGAR_DADOS_EXPIRADOS = em_ingles(ORIGEM['sp_purgar_dados_expirados'])

FN_SET_UPDATED_AT = em_ingles(v0056.FN_DEFINIR_ATUALIZADO_EM).replace('fn_definir_atualizado_em', 'fn_set_updated_at')
RENOMEAR_FN_UPDATED_AT = 'ALTER FUNCTION fn_definir_atualizado_em() RENAME TO fn_set_updated_at'
DESFAZER_FN_UPDATED_AT = 'ALTER FUNCTION fn_set_updated_at() RENAME TO fn_definir_atualizado_em'

# updated_at novo (0079): as duas tabelas tem UPDATE de negocio (tentativas/uso do OTP, conclusao do job)
TABELAS_UPDATED_AT_NOVAS = ('codigo_otp', 'execucao_job')


def tg_updated_at(tabela):
    return (f'CREATE TRIGGER tg_{tabela}_updated_at BEFORE UPDATE ON {tabela} '
            'FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()')


CRIAR = [
    RENOMEAR_FN_UPDATED_AT,
    FN_SET_UPDATED_AT,
    *(tg_updated_at(t) for t in TABELAS_UPDATED_AT_NOVAS),
    FN_CONSENTIMENTO_IMUTAVEL,
    FN_LOG_AUDITORIA_IMUTAVEL,
    FN_HISTORICO_ALTERACAO_IMUTAVEL,
    FN_LINK_ACESSO_IMUTAVEL,
    FN_PRONTUARIO_VERSAO_IMUTAVEL,
    FN_ANOTACAO_SESSAO_IMUTAVEL,
    FN_ACEITE_TERMO_IMUTAVEL,
    FN_REGISTRAR_HISTORICO,
    SP_ANONIMIZAR_CLIENTE,
    SP_APLICAR_RETENCAO_LGPD,
    SP_PURGAR_DADOS_EXPIRADOS,
]

REMOVER = [
    *(f'DROP TRIGGER IF EXISTS tg_{t}_updated_at ON {t}' for t in TABELAS_UPDATED_AT_NOVAS),
    DESFAZER_FN_UPDATED_AT,
    v0056.FN_DEFINIR_ATUALIZADO_EM,
    *ORIGEM.values(),
]
