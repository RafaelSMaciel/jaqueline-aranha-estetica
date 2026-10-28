"""Inventario LGPD (inventario_lgpd.py) x modelos x procedures x docs/LGPD.md.

Reprova: tabela nova sem classe; coluna com cara de dado pessoal sem tratamento declarado;
ANONIMIZA sem UPDATE da coluna no sp_anonimizar_cliente; APAGA sem DELETE da tabela no
esquecimento/retencao; PURGA sem a tabela no sp_purgar_dados_expirados; tabela do titular fora
do registro de operacoes (docs/LGPD.md).
"""
import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase

from aranha_estetica import inventario_lgpd as inv
from aranha_estetica.migrations.sql import v0077_retencao as procs   # versao vigente das procedures
from aranha_estetica.migrations.sql import v0077_retencao as anonimizar   # versao vigente

# nome de coluna que costuma guardar dado de pessoa (o detector e largo; falso positivo = declarar)
DETECTOR = re.compile(
    r'(^|_)(nome|email|telefone|celular|cpf|rg|endereco|cep|nascimento|profissao|foto|ip_origem|user_agent'
    r'|mensagem|comentario|respostas|alergias|contraindicacoes|historico_saude|medicamentos|observac'
    r'|texto|identificador|biografia|endpoint|acao|detalhes|dados|motivo)'
)
# colunas que o detector pega mas nao sao de pessoa (texto do catalogo, nomes congelados de produto)
NAO_PESSOAIS = {
    ('procedimento', 'nome'), ('promocao', 'nome'), ('pacote', 'nome'), ('formulario', 'nome'),
    ('compra_pacote', 'pacote_nome'), ('compra_pacote_item', 'procedimento_nome'),
    ('consentimento', 'texto'),          # texto do checkbox (igual p/ todas), nao da titular
    ('historico_alteracao', 'colunas_alteradas'),
}


def _modelos():
    return [m for m in apps.get_app_config('aranha_estetica').get_models() if m._meta.managed]


def _sem_comentarios(sql):
    return re.sub(r'--[^\n]*', '', sql)


ANONIMIZAR = _sem_comentarios(anonimizar.SP_ANONIMIZAR_CLIENTE)
ESQUECIMENTO_E_RETENCAO = ANONIMIZAR + _sem_comentarios(procs.SP_APLICAR_RETENCAO_LGPD)
PURGAR = _sem_comentarios(procs.SP_PURGAR_DADOS_EXPIRADOS)


class InventarioLgpdTests(SimpleTestCase):
    def test_toda_tabela_tem_classe(self):
        tabelas = {m._meta.db_table for m in _modelos()}
        self.assertEqual(sorted(tabelas - set(inv.TABELAS)), [], 'tabela nova: classifique em inventario_lgpd')
        self.assertEqual(sorted(set(inv.TABELAS) - tabelas), [], 'tabela que nao existe mais no inventario')

    def test_coluna_pessoal_tem_tratamento(self):
        faltando = []
        for m in _modelos():
            for f in m._meta.concrete_fields:
                chave = (m._meta.db_table, f.column)
                if f.is_relation or chave in NAO_PESSOAIS or chave in inv.COLUNAS:
                    continue
                if DETECTOR.search(f.column):
                    faltando.append('.'.join(chave))
        self.assertEqual(faltando, [], 'declare o tratamento em inventario_lgpd.COLUNAS (ou NAO_PESSOAIS)')

    def test_colunas_declaradas_existem(self):
        existentes = {(m._meta.db_table, f.column) for m in _modelos() for f in m._meta.concrete_fields}
        self.assertEqual(sorted(set(inv.COLUNAS) - existentes), [])

    def test_anonimiza_e_um_update_da_coluna_no_esquecimento(self):
        ruins = []
        for (tabela, coluna), _t in inv.COLUNAS.items():
            if inv.tratamento(tabela, coluna) != inv.ANONIMIZA:
                continue
            updates = re.findall(rf'UPDATE\s+(?:public\.)?{tabela}\b(.*?);', ANONIMIZAR, re.S | re.I)
            if not any(re.search(rf'\b{coluna}\s*=', u) for u in updates):
                ruins.append(f'{tabela}.{coluna}')
        self.assertEqual(ruins, [], 'sp_anonimizar_cliente nao limpa a coluna marcada ANONIMIZA')

    def test_apaga_e_um_delete_no_esquecimento_ou_retencao(self):
        ruins = sorted({
            t for (t, c) in inv.COLUNAS if inv.tratamento(t, c) == inv.APAGA
            and not re.search(rf'DELETE\s+FROM\s+(?:public\.)?{t}\b', ESQUECIMENTO_E_RETENCAO, re.I)
        })
        self.assertEqual(ruins, [])

    def test_purga_esta_no_housekeeping(self):
        ruins = sorted({
            t for (t, c) in inv.COLUNAS if inv.tratamento(t, c) == inv.PURGA
            and not re.search(rf'(DELETE\s+FROM|UPDATE)\s+(?:public\.)?{t}\b', PURGAR, re.I)
        })
        self.assertEqual(ruins, [])

    def test_cadastro_sem_dado_sem_finalidade(self):
        """RG, profissao e foto da cliente sairam (0071, art. 6º III): nem coluna, nem inventario, nem
        procedure; o link_acesso nao tem mais alvo atendimento (0069)."""
        colunas = {f.column for f in apps.get_model('aranha_estetica', 'Cliente')._meta.concrete_fields}
        self.assertFalse({'rg', 'profissao', 'url_foto'} & colunas)
        self.assertFalse({('cliente', c) for c in ('rg', 'profissao', 'url_foto')} & set(inv.COLUNAS))
        for removida in (r'\brg\b', r'\bprofissao\b', r'\burl_foto\b', r'OR\s+atendimento_id\s*=\s*ANY'):
            with self.subTest(coluna=removida):
                self.assertNotRegex(ANONIMIZAR, removida)

    def test_retem_tem_motivo(self):
        sem = [f'{t}.{c}' for (t, c), v in inv.COLUNAS.items()
               if inv.tratamento(t, c) == inv.RETEM and not (isinstance(v, tuple) and v[1])]
        self.assertEqual(sem, [])

    def test_tabela_do_titular_esta_no_registro_de_operacoes(self):
        doc = (Path(settings.BASE_DIR) / 'docs' / 'LGPD.md').read_text(encoding='utf-8')
        fora = sorted(t for t, classe in inv.TABELAS.items() if classe == inv.TITULAR and f'`{t}`' not in doc)
        self.assertEqual(fora, [], 'registre a tabela no docs/LGPD.md §2')
