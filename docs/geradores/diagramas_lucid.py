# -*- coding: utf-8 -*-
"""Diagramas do sistema no Lucidchart: MER, DER, casos de uso e classes.

O Lucid é a fonte dos diagramas (editáveis lá). Este script garante que eles batem entre si e com
o código: DER e classes saem dos models (tabelas, colunas, chaves, métodos), o MER usa os mesmos
nomes de coluna, e os casos de uso e o MER conceitual ficam descritos aqui (MER_*, CASOS_DE_USO).

Para cada página ele calcula o layout com o Graphviz e escreve um script do Lucid (JavaScript) que
monta a página com os blocos UML nativos (classe, entidade, relacionamento, atributo, ator, caso de
uso, fronteira) e o DER nativo (entidades com crow's foot). Os scripts são rodados no documento
do Lucid pela ferramenta de scripts do conector; as imagens exportadas vão para docs/diagramas/.

Uso (Python do projeto, lê os models; Graphviz no PATH ou em GRAPHVIZ_DOT):
    python docs/geradores/diagramas_lucid.py <pasta de saída>
Gera <pasta>/<diagrama>/<nn-pagina>.js e <pasta>/paginas.json (título de cada página).
"""
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import typing
from collections import OrderedDict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

# ─── Temas (iguais no MER, no DER e nas classes) ───────────────────────────────────────────────
TEMAS = OrderedDict([
    ('agendamento', ('Atendimento e lista de espera', ['atendimento', 'lista_espera'])),
    ('catalogo', ('Profissionais e catálogo', ['profissional', 'procedimento', 'habilitacao', 'preco', 'promocao'])),
    ('expediente', ('Expediente e folgas', ['agenda_horario', 'agenda_excecao'])),
    ('clientes', ('Clientes e prontuário', ['cliente', 'prontuario', 'prontuario_versao', 'anotacao_sessao'])),
    ('formularios', ('Fichas de anamnese', ['formulario', 'resposta_formulario'])),
    ('pacotes', ('Pacotes', ['pacote', 'pacote_item', 'compra_pacote', 'compra_pacote_item', 'consumo_sessao'])),
    ('comissao', ('Comissão e carteira', ['regra_comissao', 'movimento_comissao', 'carteira', 'movimento_carteira'])),
    ('comunicacao', ('Comunicação e links', ['notificacao', 'avaliacao_nps', 'link_acesso', 'assinatura_push'])),
    ('lgpd', ('LGPD e termos', ['consentimento', 'versao_termo', 'aceite_termo'])),
    ('acesso', ('Acesso e auditoria', ['usuario', 'log_auditoria', 'historico_alteracao', 'codigo_otp', 'configuracao',
                                       'execucao_job'])),
])

# ─── Modelo físico (models) ─────────────────────────────────────────────────────────────────────


def _django():
    sys.path.insert(0, str(RAIZ))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clinica.settings')
    import django
    django.setup()
    from django.apps import apps
    from django.db.backends.postgresql.base import DatabaseWrapper
    pg = DatabaseWrapper({
        'NAME': 'x', 'USER': '', 'PASSWORD': '', 'HOST': '', 'PORT': '', 'OPTIONS': {}, 'TIME_ZONE': None,
        'CONN_MAX_AGE': 0, 'CONN_HEALTH_CHECKS': False, 'AUTOCOMMIT': True, 'ATOMIC_REQUESTS': False, 'TEST': {},
    }, 'documentacao')
    return {m._meta.db_table: m for m in apps.get_app_config('aranha_estetica').get_models()}, pg


METADADOS = ('created_at', 'updated_at', 'deleted_at', 'created_by_id', 'created_by_name')


def _colunas(model):
    def chave(f):
        return METADADOS.index(f.column) + 1 if f.column in METADADOS else 0
    return sorted(model._meta.concrete_fields, key=chave)


def _tipo_sql(field, pg):
    tipo = (field.db_type(pg) or '').replace('timestamp with time zone', 'timestamptz')
    tipo = re.sub(r'numeric\((\d+), (\d+)\)', r'numeric(\1,\2)', tipo)
    return tipo.replace('bigserial', 'bigint').replace('serial', 'integer')


def _tipo_uml_sql(tipo):
    base = tipo.split('(')[0]
    return {
        'varchar': 'String', 'text': 'String', 'char': 'String', 'inet': 'String', 'boolean': 'Boolean',
        'smallint': 'Integer', 'integer': 'Integer', 'bigint': 'Integer', 'numeric': 'Decimal', 'date': 'Date',
        'timestamptz': 'DateTime', 'time': 'Time', 'jsonb': 'JSON', 'uuid': 'UUID', 'bytea': 'Bytes',
        'double precision': 'Real',
    }.get(base, base)


def _enums():
    """{tupla de choices: nome} das TextChoices declaradas em models/."""
    import importlib
    import pkgutil

    from django.db import models as dj

    import aranha_estetica.models as pacote
    achados = {}
    for info in pkgutil.iter_modules(pacote.__path__):
        mod = importlib.import_module(f'aranha_estetica.models.{info.name}')
        for nome, obj in vars(mod).items():
            if inspect.isclass(obj) and issubclass(obj, dj.Choices) and obj.__module__ == mod.__name__:
                achados[tuple(obj.choices)] = (nome, [str(v) for v in obj.values])
    return achados


# ─── Operações das classes (assinatura UML: + nome(param: Tipo): Retorno) ──────────────────────
# Protocolo de autenticação do Django e ganchos do ORM ficam fora: não são regra do domínio.
FORA = {'clean', 'save', 'delete', 'full_clean', 'natural_key', 'get_absolute_url', 'refresh_from_db',
        'is_active', 'is_staff', 'first_name', 'has_perm', 'has_module_perms'}

# Assinaturas dos métodos que o código ainda não anota (conferidas no corpo de cada um).
ASSINATURAS = {
    'AgendaExcecao.periodo_display': ([], 'String'),
    'AgendaExcecao.ocorrencias': ([('range_inicio', 'DateTime'), ('range_fim', 'DateTime')], '(DateTime, DateTime) [*]'),
    'Cliente.registrar_cancelamento_tardio': ([], 'void'),
    'Cliente.registrar_falta': ([], 'void'),
    'Cliente.resetar_faltas': ([], 'void'),
    'Cliente.soft_delete': ([], 'void'),
    'Consentimento.par': ([], '(String, String)'),
    'Promocao.esta_vigente': ([], 'Boolean'),
    'Atendimento.confirmar': ([('by_user', 'Usuario [0..1]')], 'void'),
    'Atendimento.cancelar': ([('motivo', 'String'), ('by_user', 'Usuario [0..1]'), ('pela_cliente', 'Boolean')], 'void'),
    'Atendimento.marcar_realizado': ([('by_user', 'Usuario [0..1]')], 'void'),
    'Atendimento.marcar_falta': ([('by_user', 'Usuario [0..1]')], 'void'),
    'Atendimento.marcar_reagendado': ([('by_user', 'Usuario [0..1]'), ('pela_cliente', 'Boolean')], 'void'),
    'Atendimento.aprovar': ([('by_user', 'Usuario [0..1]')], 'void'),
    'Atendimento.desfazer_realizacao': ([('destino', 'StatusAtendimento'), ('motivo', 'String'),
                                         ('by_user', 'Usuario [0..1]')], 'void'),
    'ProntuarioVersao.registrar': ([('prontuario', 'Prontuario'), ('usuario', 'Usuario [0..1]')], 'ProntuarioVersao [0..1]'),
    'VersaoTermo.lgpd_vigente': ([], 'VersaoTermo [0..1]'),
    'VersaoTermo.saude_vigente': ([], 'VersaoTermo [0..1]'),
    'VersaoTermo.texto_saude_vigente': ([], '(VersaoTermo [0..1], String)'),
    'VersaoTermo.sha256_conteudo': ([], 'String'),
    'AceiteTermo.registrar': ([('cliente', 'Cliente'), ('versao_termo', 'VersaoTermo'), ('request', 'HttpRequest [0..1]'),
                               ('atendimento', 'Atendimento [0..1]')], 'AceiteTermo [0..1]'),
    'CompraPacote.vender': ([('pacote', 'Pacote'), ('cliente', 'Cliente'), ('valor_pago', 'Decimal')], 'CompraPacote'),
    'CompraPacote.congelar_itens': ([], 'CompraPacoteItem [*]'),
    'CompraPacote.saldo_por_procedimento': ([], 'Map [*]'),
    'CompraPacote.sessoes_restantes': ([], 'Integer'),
    'CompraPacote.verificar_finalizacao': ([], 'void'),
    'CodigoOtp.gerar_sms': ([('telefone', 'String'), ('ip', 'String [0..1]'), ('proposito', 'String')], '(String, CodigoOtp)'),
    'CodigoOtp.verificar_sms': ([('telefone', 'String'), ('codigo', 'String'), ('proposito', 'String')], '(Boolean, String)'),
    'CodigoOtp.pode_reenviar': ([('email', 'String'), ('proposito', 'String')], 'Boolean'),
    'CodigoOtp.gerar': ([('email', 'String'), ('ip', 'String [0..1]'), ('proposito', 'String'), ('canal', 'String'),
                         ('telefone', 'String [0..1]')], '(String, CodigoOtp)'),
    'CodigoOtp.verificar': ([('email', 'String'), ('codigo', 'String'), ('proposito', 'String')], '(Boolean, String)'),
    'Configuracao.ler': ([('chave', 'String')], 'Object'),
    'FormularioAnamnese.aplica_a': ([('procedimento', 'Procedimento')], 'Boolean'),
    'RespostaAnamnese.respondida': ([], 'Boolean'),
    'LinkAcesso.alvo_id_rotulo': ([], 'String'),
    'LinkAcesso.alvo': ([], 'Object [0..1]'),
    'LinkAcesso.expirado': ([], 'Boolean'),
    'LinkAcesso.revogado': ([], 'Boolean'),
    'MovimentoComissao.status': ([], 'String [0..1]'),
    'MovimentoComissao.pago_em': ([], 'DateTime [0..1]'),
    'MovimentoComissao.estornado_em': ([], 'DateTime [0..1]'),
}

_PY_UML = {'str': 'String', 'int': 'Integer', 'bool': 'Boolean', 'Decimal': 'Decimal', 'date': 'Date',
           'datetime': 'DateTime', 'time': 'Time', 'dict': 'Map', 'float': 'Real', 'NoneType': 'void', 'None': 'void',
           'Any': 'Object', 'object': 'Object'}


def _tipo_uml_py(anot):
    """Anotação Python -> tipo UML (X | None -> X [0..1]; list[X] -> X [*]; tuple -> (A, B))."""
    if anot is inspect.Signature.empty:
        return None
    if anot is None or anot is type(None):
        return 'void'
    if isinstance(anot, str):
        texto = anot
    else:
        origem = typing.get_origin(anot)
        if origem in (list, set, tuple, dict) or origem is typing.Union or str(origem) == "<class 'types.UnionType'>":
            texto = str(anot)
        else:
            texto = getattr(anot, '__name__', str(anot))
    texto = texto.replace('typing.', '').replace('decimal.', '').replace('datetime.', '')
    texto = re.sub(r"<class '([\w.]+)'>", r'\1', texto)
    texto = re.sub(r'\b[\w.]+\.(\w+)', r'\1', texto)
    m = re.fullmatch(r'Optional\[(.+)\]', texto) or re.fullmatch(r'(.+) \| None', texto)
    if m:
        return f'{_tipo_uml_py(m.group(1))} [0..1]'
    m = re.fullmatch(r'(?:list|List|set|Set)\[(.+)\]', texto)
    if m:
        return f'{_tipo_uml_py(m.group(1))} [*]'
    m = re.fullmatch(r'(?:tuple|Tuple)\[(.+)\]', texto)
    if m:
        return '(' + ', '.join(_tipo_uml_py(p.strip()) for p in m.group(1).split(',')) + ')'
    if re.fullmatch(r'(?:dict|Dict)\[.+\]', texto):
        return 'Map'
    return _PY_UML.get(texto, texto)


def _operacoes(cls):
    """(atributos derivados, operações) da classe, em notação UML. Falta de tipo = erro (completar ASSINATURAS)."""
    derivados, ops, faltam = [], [], []
    for nome, obj in cls.__dict__.items():
        if nome.startswith('_') or nome in FORA or (nome.startswith('get_') and nome.endswith('_display')):
            continue
        estatico = isinstance(obj, (staticmethod, classmethod))
        func = obj.fget if isinstance(obj, property) else (obj.__func__ if estatico else obj)
        if not inspect.isfunction(func):
            continue
        chave = f'{cls.__name__}.{nome}'
        if chave in ASSINATURAS:
            params, ret = ASSINATURAS[chave]
        else:
            sig = inspect.signature(func)
            params, ret = [], _tipo_uml_py(sig.return_annotation)
            for p in sig.parameters.values():
                if p.name in ('self', 'cls') or p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
                    continue
                tipo = _tipo_uml_py(p.annotation)
                if tipo is None:
                    faltam.append(chave)
                params.append((p.name, tipo))
            if ret is None:
                faltam.append(chave)
        if isinstance(obj, property):
            derivados.append(f'+ /{nome}: {ret} {{readOnly}}')
        else:
            assin = ', '.join(f'{n}: {t}' for n, t in params)
            ops.append((f'+ {nome}({assin}): {ret}', estatico))
    return derivados, ops, sorted(set(faltam))


def modelo():
    """Tabelas, colunas, chaves e relações extraídas dos models."""
    por_tabela, pg = _django()
    sem_tema = sorted(set(por_tabela) - {t for _, ts in TEMAS.values() for t in ts})
    if sem_tema:
        raise SystemExit(f'tabelas sem tema em TEMAS: {sem_tema}')
    enums = _enums()
    tabelas, faltam = OrderedDict(), []
    for tema, (_, nomes) in TEMAS.items():
        for t in nomes:
            m = por_tabela[t]
            cols = []
            for f in _colunas(m):
                tipo = _tipo_sql(f, pg)
                enum = enums.get(tuple(f.choices))[0] if f.choices and tuple(f.choices) in enums else None
                cifrado = 'Cifrad' in type(f).__name__
                cols.append({
                    'col': f.column, 'sql': tipo, 'uml': enum or _tipo_uml_sql(tipo), 'enum': enum, 'nulo': f.null,
                    'pk': f.primary_key, 'unico': bool(f.unique and not f.primary_key), 'cifrado': cifrado,
                    'fk': f.related_model._meta.db_table if f.is_relation else None,
                    'um_para_um': bool(f.is_relation and (f.one_to_one or f.unique)),
                    'cascata': bool(f.is_relation and f.remote_field.on_delete.__name__ == 'CASCADE'),
                })
            derivados, ops, falta = _operacoes(m)
            faltam += falta
            tabelas[t] = {'classe': m.__name__, 'tema': tema, 'cols': cols, 'derivados': derivados, 'ops': ops,
                          'enums': {c['enum']: enums[tuple(f.choices)][1] for f, c in zip(_colunas(m), cols) if c['enum']}}
    if faltam:
        raise SystemExit('métodos sem tipo (anote no código ou complete ASSINATURAS): ' + ', '.join(faltam))
    return tabelas


# ─── Layout (Graphviz) ──────────────────────────────────────────────────────────────────────────

def _dot():
    return os.environ.get('GRAPHVIZ_DOT') or shutil.which('dot') or 'dot'


def layout(nos, arestas, *, rankdir='TB', nodesep=0.6, ranksep=0.9, clusters=None, extra=''):
    """nos: {id: (w, h)} em px; arestas: [(a, b, peso)]. Devolve {id: (x, y)} do canto superior esquerdo
    e {cluster: (x, y, w, h)}. 1 px = 1 pt do Graphviz."""
    linhas = [f'digraph G {{ graph [rankdir={rankdir}, nodesep={nodesep}, ranksep={ranksep}, splines=false{extra}];',
              'node [shape=box, fixedsize=true, label=""];']
    dentro = set()
    for nome, (membros, opcoes) in (clusters or {}).items():
        linhas.append(f'subgraph "cluster_{nome}" {{ graph [margin=40{opcoes}];')
        for n in membros:
            w, h = nos[n]
            linhas.append(f'"{n}" [width={w / 72:.3f}, height={h / 72:.3f}];')
            dentro.add(n)
        linhas.append('}')
    for n, (w, h) in nos.items():
        if n not in dentro:
            linhas.append(f'"{n}" [width={w / 72:.3f}, height={h / 72:.3f}];')
    for a, b, *resto in arestas:
        peso = resto[0] if resto else 1
        restricao = resto[1] if len(resto) > 1 else True
        linhas.append(f'"{a}" -> "{b}" [weight={peso}{", constraint=false" if not restricao else ""}];')
    linhas.append('}')
    saida = subprocess.run([_dot(), '-Tjson'], input='\n'.join(linhas), capture_output=True, text=True, encoding='utf-8')
    if saida.returncode:
        raise SystemExit(saida.stderr)
    g = json.loads(saida.stdout)
    altura = float(g['bb'].split(',')[3])
    pos, caixas = {}, {}
    for obj in g.get('objects', []):
        if 'pos' in obj:
            x, y = map(float, obj['pos'].split(','))
            w, h = nos[obj['name']]
            pos[obj['name']] = (round(x - w / 2), round(altura - y - h / 2))
        elif obj.get('name', '').startswith('cluster_') and 'bb' in obj:
            x1, y1, x2, y2 = map(float, obj['bb'].split(','))
            caixas[obj['name'][8:]] = (round(x1), round(altura - y2), round(x2 - x1), round(y2 - y1))
    return pos, caixas


# Medidas do bloco de classe do Lucid (fonte padrão): largura média de um caractere e altura de uma linha
LARGURA_CHAR, LINHA = 8.6, 22


def _caixa_classe(nome, attrs, ops):
    linhas = [nome] + attrs + [o for o, _ in ops]
    w = max(200, int(max(len(t) for t in linhas) * LARGURA_CHAR) + 52)
    h = 133 + LINHA * len(attrs) + LINHA * max(0, len(ops) - 1) + 16   # altura que o Lucid dá ao bloco
    return w, h


def _atributos(t, tabelas):
    attrs = []
    for c in tabelas[t]['cols']:
        if c['fk']:
            continue   # vira associação (o papel tem o nome da coluna sem _id)
        mods = []
        if c['pk']:
            mods.append('id')
        if c['unico']:
            mods.append('unique')
        if c['cifrado']:
            mods.append('cifrado')
        mult = ' [0..1]' if c['nulo'] else ''
        sufixo = f' {{{", ".join(mods)}}}' if mods else ''
        attrs.append(f"- {c['col']}: {c['uml']}{mult}{sufixo}")
    return attrs + tabelas[t]['derivados']


def pagina_classes(tabelas, nomes, *, referencias=True):
    """Classes completas das tabelas em `nomes` + classes vizinhas (só o nome) + enumerações usadas."""
    nos, dados, arestas, enums = {}, [], [], OrderedDict()
    conjunto = set(nomes)
    externas = set()
    for t in nomes:
        for c in tabelas[t]['cols']:
            if c['fk'] and c['fk'] not in conjunto:
                externas.add(c['fk'])
        for e, valores in tabelas[t]['enums'].items():
            enums[e] = valores
    for t in nomes:
        info = tabelas[t]
        attrs, ops = _atributos(t, tabelas), info['ops']
        nos[t] = _caixa_classe(info['classe'], attrs, ops)
        dados.append({'id': t, 'k': 'C', 'nome': info['classe'], 'attrs': attrs, 'ops': [o for o, _ in ops],
                      'estaticas': [o for o, s in ops if s]})
    if referencias:
        for t in sorted(externas):
            nos[t] = (max(150, int(len(tabelas[t]['classe']) * 10) + 50), 121)   # altura mínima do Lucid
            dados.append({'id': t, 'k': 'R', 'nome': tabelas[t]['classe']})
    for e, valores in enums.items():
        nos['enum_' + e] = (max(180, int(max(len(e) + 15, *map(len, valores)) * LARGURA_CHAR) + 40),
                            160 + LINHA * len(valores))
        dados.append({'id': 'enum_' + e, 'k': 'E', 'nome': '«enumeration»\n' + e, 'attrs': valores})
    for t in nomes if not referencias else list(conjunto | externas):
        for c in tabelas[t]['cols']:
            pai = c['fk']
            if not pai or pai not in nos or (t not in conjunto and pai not in conjunto):
                continue
            filho_mult = '0..1' if c['um_para_um'] else '0..*'
            pai_mult = '0..1' if c['nulo'] else '1'
            papel = c['col'][:-3] if c['col'].endswith('_id') else c['col']
            if c['cascata'] and not c['nulo']:
                arestas.append({'de': pai, 'para': t, 'tipo': 'composition', 'm1': pai_mult, 'm2': filho_mult, 'rot': ''})
            else:
                arestas.append({'de': t, 'para': pai, 'tipo': 'directedAssociation', 'm1': filho_mult, 'm2': pai_mult,
                                'rot': papel})
    for t in nomes:
        for c in tabelas[t]['cols']:
            if c['enum']:
                arestas.append({'de': t, 'para': 'enum_' + c['enum'], 'tipo': 'dependency', 'm1': '', 'm2': '', 'rot': ''})
    vistos, unicas = set(), []
    for a in arestas:
        chave = (a['de'], a['para'], a['rot'], a['tipo'])
        if chave not in vistos:
            vistos.add(chave)
            unicas.append(a)
    grafo = [(a['de'], a['para'], 1 if a['tipo'] != 'dependency' else 0) for a in unicas if a['de'] != a['para']]
    pos, _ = layout(nos, grafo, rankdir='TB', nodesep=0.9, ranksep=1.1)
    for d in dados:
        d['x'], d['y'] = pos[d['id']]
        d['w'], d['h'] = nos[d['id']]
    return {'nos': dados, 'arestas': unicas}


# ─── DER (ERD nativo do Lucid, crow's foot) ─────────────────────────────────────────────────────


def pagina_der(tabelas, nomes):
    conjunto = set(nomes)
    externas = set()
    for t in nomes:
        for c in tabelas[t]['cols']:
            if c['fk'] and c['fk'] not in conjunto:
                externas.add(c['fk'])
    nos, ents, rels = {}, [], []
    for t in list(nomes) + sorted(externas):
        completa = t in conjunto
        campos = []
        for c in tabelas[t]['cols'] if completa else [c for c in tabelas[t]['cols'] if c['pk']]:
            chave = 'PK' if c['pk'] else ('FK' if c['fk'] else ('AK' if c['unico'] else ''))
            if c['fk'] and c['unico']:
                chave = 'FK,AK'
            campos.append({'name': c['col'], 'type': c['sql'], 'key': chave, 'nullable': c['nulo']})
        nos[t] = (495, 40 + 32 * len(campos))   # tamanho real da tabela do Lucid (colunas 55 + 215 + 225)
        ents.append({'key': t, 'name': t, 'fields': campos, 'externa': not completa})
    for t in nomes:
        for c in tabelas[t]['cols']:
            if c['fk'] and c['fk'] in nos:
                rels.append({'primaryEntity': c['fk'], 'foreignEntity': t, 'primaryField': 'id', 'foreignField': c['col'],
                             'primaryCardinality': 'zeroOrOne' if c['nulo'] else 'exactlyOne',
                             'foreignCardinality': 'zeroOrOne' if c['um_para_um'] else 'zeroOrMany'})
    grafo = [(r['primaryEntity'], r['foreignEntity'], 1) for r in rels if r['primaryEntity'] != r['foreignEntity']]
    pos, _ = layout(nos, grafo, rankdir='LR', nodesep=0.8, ranksep=1.6)
    for e in ents:
        e['location'] = {'x': pos[e['key']][0], 'y': pos[e['key']][1]}
    return {'entities': ents, 'relationships': rels}


# ─── MER conceitual (notação de Chen, cardinalidade (mín,máx) de Heuser) ───────────────────────
# Entidades: nome -> (tema, atributos). Atributos = colunas do DER (o primeiro é o identificador).
MER_ENTIDADES = OrderedDict([
    ('Atendimento', ('agendamento', ['id', 'data_hora_inicio', 'data_hora_fim', 'status', 'valor_cobrado'])),
    ('Lista de espera', ('agendamento', ['id', 'nome_contato', 'data_desejada', 'turno_desejado'])),
    ('Profissional', ('catalogo', ['id', 'nome', 'especialidade', 'eh_ativo'])),
    ('Procedimento', ('catalogo', ['id', 'nome', 'duracao_minutos', 'categoria', 'eh_ativo'])),
    ('Preço', ('catalogo', ['id', 'valor', 'data_inicio_vigencia'])),
    ('Promoção', ('catalogo', ['id', 'nome', 'percentual_desconto', 'data_inicio', 'data_fim'])),
    ('Expediente', ('expediente', ['id', 'dia_semana', 'hora_inicio', 'hora_fim'])),
    ('Exceção de agenda', ('expediente', ['id', 'tipo', 'data_inicio', 'data_fim', 'motivo'])),
    ('Cliente', ('clientes', ['id', 'nome', 'telefone', 'email', 'data_nascimento'])),
    ('Prontuário', ('clientes', ['id', 'alergias', 'contraindicacoes', 'medicamentos_uso'])),
    ('Anotação de sessão', ('clientes', ['id', 'texto', 'created_at'])),
    ('Formulário', ('formularios', ['id', 'nome', 'tipo', 'perguntas'])),
    ('Pacote', ('pacotes', ['id', 'nome', 'valor_total', 'validade_meses'])),
    ('Compra de pacote', ('pacotes', ['id', 'valor_pago', 'status', 'data_expiracao'])),
    ('Regra de comissão', ('comissao', ['id', 'percentual', 'valor_fixo', 'eh_ativo'])),
    ('Comissão', ('comissao', ['id', 'tipo', 'valor', 'percentual_aplicado'])),
    ('Carteira', ('comissao', ['id', 'valor_saldo'])),
    ('Movimento de carteira', ('comissao', ['id', 'tipo', 'origem', 'valor'])),
    ('Notificação', ('comunicacao', ['id', 'tipo', 'canal', 'status', 'enviado_em'])),
    ('Avaliação NPS', ('comunicacao', ['id', 'nota', 'comentario'])),
    ('Link de acesso', ('comunicacao', ['id', 'finalidade', 'expira_em', 'usado_em'])),
    ('Usuário', ('acesso', ['id', 'nome', 'email', 'papel'])),
    ('Termo', ('lgpd', ['id', 'tipo', 'titulo', 'versao', 'data_inicio_vigencia'])),
    ('Consentimento', ('lgpd', ['id', 'finalidade', 'canal', 'eh_concedido', 'registrado_em'])),
    ('Log de auditoria', ('acesso', ['id', 'acao', 'tabela', 'created_at'])),
])
# Relacionamentos: (nome, tema dono, entidade A, card. de A, entidade B, card. de B, atributos do relacionamento)
# O relacionamento aparece só na página do seu tema (e no MER completo).
# card. de A = quantas ocorrências de A se ligam a UMA ocorrência de B (escrita junto de A).
MER_RELACOES = [
    ('habilita', 'catalogo', 'Profissional', '(0,N)', 'Procedimento', '(0,N)', []),
    ('tem preço', 'catalogo', 'Procedimento', '(1,1)', 'Preço', '(0,N)', []),
    ('diferencia', 'catalogo', 'Profissional', '(0,1)', 'Preço', '(0,N)', []),
    ('aplica', 'catalogo', 'Procedimento', '(0,1)', 'Promoção', '(0,N)', []),
    ('trabalha em', 'expediente', 'Profissional', '(1,1)', 'Expediente', '(0,N)', []),
    ('folga ou bloqueia', 'expediente', 'Profissional', '(0,1)', 'Exceção de agenda', '(0,N)', []),
    ('agenda', 'agendamento', 'Cliente', '(1,1)', 'Atendimento', '(0,N)', []),
    ('realiza', 'agendamento', 'Profissional', '(1,1)', 'Atendimento', '(0,N)', []),
    ('refere-se a', 'agendamento', 'Procedimento', '(1,1)', 'Atendimento', '(0,N)', []),
    ('desconta', 'agendamento', 'Promoção', '(0,1)', 'Atendimento', '(0,N)', []),
    ('aguarda', 'agendamento', 'Procedimento', '(1,1)', 'Lista de espera', '(0,N)', []),
    ('entra em', 'agendamento', 'Cliente', '(0,1)', 'Lista de espera', '(0,N)', []),
    ('indica', 'clientes', 'Cliente', '(0,1)', 'Cliente', '(0,N)', []),
    ('possui', 'clientes', 'Cliente', '(1,1)', 'Prontuário', '(0,1)', []),
    ('responde', 'formularios', 'Cliente', '(0,N)', 'Formulário', '(0,N)', ['respostas', 'respondido_em']),
    ('é anotado em', 'clientes', 'Atendimento', '(1,1)', 'Anotação de sessão', '(0,N)', []),
    ('contém', 'pacotes', 'Pacote', '(0,N)', 'Procedimento', '(1,N)', ['qtd_sessoes']),
    ('compra', 'pacotes', 'Cliente', '(1,1)', 'Compra de pacote', '(0,N)', []),
    ('é vendido em', 'pacotes', 'Pacote', '(1,1)', 'Compra de pacote', '(0,N)', []),
    ('consome sessão', 'pacotes', 'Compra de pacote', '(0,1)', 'Atendimento', '(0,N)', []),
    ('gera', 'comissao', 'Atendimento', '(1,1)', 'Comissão', '(0,N)', []),
    ('recebe', 'comissao', 'Profissional', '(1,1)', 'Comissão', '(0,N)', []),
    ('calcula', 'comissao', 'Regra de comissão', '(0,1)', 'Comissão', '(0,N)', []),
    ('tem regra', 'comissao', 'Profissional', '(0,1)', 'Regra de comissão', '(0,N)', []),
    ('tem carteira', 'comissao', 'Cliente', '(1,1)', 'Carteira', '(0,1)', []),
    ('registra', 'comissao', 'Carteira', '(1,1)', 'Movimento de carteira', '(0,N)', []),
    ('notifica', 'comunicacao', 'Atendimento', '(1,1)', 'Notificação', '(0,N)', []),
    ('é avaliado em', 'comunicacao', 'Atendimento', '(1,1)', 'Avaliação NPS', '(0,1)', []),
    ('dá acesso por', 'comunicacao', 'Notificação', '(0,1)', 'Link de acesso', '(0,N)', []),
    ('recebe link', 'comunicacao', 'Cliente', '(0,1)', 'Link de acesso', '(0,N)', []),
    ('é login de', 'acesso', 'Profissional', '(0,1)', 'Usuário', '(0,1)', []),
    ('aceita', 'lgpd', 'Cliente', '(0,N)', 'Termo', '(0,N)', ['ip_origem', 'conteudo_hash', 'created_at']),
    ('exige', 'lgpd', 'Procedimento', '(0,1)', 'Termo', '(0,N)', []),
    ('concede', 'lgpd', 'Cliente', '(1,1)', 'Consentimento', '(0,N)', []),
    ('registra ação', 'acesso', 'Usuário', '(0,1)', 'Log de auditoria', '(0,N)', []),
]
ATTR_W, ATTR_H, ENT_W, ENT_H, REL_W, REL_H = 150, 50, 190, 70, 170, 96


def _larg_attr(txt):
    return max(ATTR_W, int(len(txt) * 8.8) + 36)


def _anel(cx, cy, larguras, ocupados, rx, ry, caixa, folga=32):
    """n centros de atributo numa elipse em volta de (cx, cy), distribuídos nos arcos livres entre as linhas
    (ângulos `ocupados`, em graus) com `folga` de cada lado; sem linhas, a volta inteira a partir do alto."""
    import math
    n = len(larguras)
    if not n:
        return []
    if not ocupados:
        angulos = [(90 - i * 360 / n) % 360 for i in range(n)]
    else:
        lin = sorted(set(round(o) % 360 for o in ocupados))
        arcos = []   # (início, comprimento útil) de cada vão entre linhas consecutivas, sentido anti-horário
        for i, ini in enumerate(lin):
            fim = lin[(i + 1) % len(lin)] + (360 if i + 1 == len(lin) else 0)
            util = (fim - ini) - 2 * folga
            if util > 0:
                arcos.append((ini + folga, util))
        if not arcos:   # linhas para todo lado: usa os maiores vãos mesmo sem folga
            arcos = [(lin[i], (lin[(i + 1) % len(lin)] + (360 if i + 1 == len(lin) else 0)) - lin[i])
                     for i in range(len(lin))]
        total = sum(c for _, c in arcos)
        cotas = [n * c / total for _, c in arcos]
        qtd = [int(q) for q in cotas]
        for i in sorted(range(len(arcos)), key=lambda i: cotas[i] - qtd[i], reverse=True)[:n - sum(qtd)]:
            qtd[i] += 1
        angulos = []
        for (ini, comp), k in zip(arcos, qtd):
            angulos += [(ini + comp * (j + 0.5) / k) % 360 for j in range(k)]
    angulos.sort(key=lambda a: (90 - a) % 360)   # o identificador fica no alto, os demais em sentido horário
    # cada atributo sai do anel e, se encostar num já colocado ou na caixa do centro, vai mais para fora
    colocados, saida = [], []
    for a, w in zip(angulos, larguras):
        f = 1.0
        while True:
            x, y = cx + rx * f * math.cos(math.radians(a)), cy - ry * f * math.sin(math.radians(a))
            bate = any(abs(x - px) < (w + pw) / 2 + 14 and abs(y - py) < ATTR_H + 12 for px, py, pw in colocados)
            bate = bate or (abs(x - cx) < (caixa[0] + w) / 2 + 12 and abs(y - cy) < (caixa[1] + ATTR_H) / 2 + 12)
            if not bate or f > 3:
                break
            f += 0.1
        colocados.append((x, y, w))
        saida.append((round(x), round(y)))
    return saida


def pagina_mer(tema=None):
    """MER do tema (entidades do tema com atributos + vizinhas sem atributos) ou completo (tema=None)."""
    import math
    no_tema = {e for e, (t, _) in MER_ENTIDADES.items() if tema is None or t == tema}
    rels = [r for r in MER_RELACOES if tema is None or r[1] == tema]
    usadas = no_tema | {r[2] for r in rels} | {r[4] for r in rels}
    rx_e, ry_e = ENT_W / 2 + ATTR_W / 2 + 30, ENT_H / 2 + ATTR_H / 2 + 52
    rx_r, ry_r = REL_W / 2 + ATTR_W / 2 + 15, REL_H / 2 + ATTR_H / 2 + 28
    nos, attrs = {}, {}
    grau = {}
    for r in rels:
        grau[r[2]] = grau.get(r[2], 0) + 1
        grau[r[4]] = grau.get(r[4], 0) + 1
    # entidade com muitas ligações: anel maior (os atributos ficam longe das linhas e entre si)
    escala = {e: 1 + 0.14 * max(0, grau.get(e, 0) - 3) for e in MER_ENTIDADES}
    for e in [e for e in MER_ENTIDADES if e in usadas]:
        attrs[e] = MER_ENTIDADES[e][1] if e in no_tema else []
        k = escala[e] * 1.55
        nos[e] = ((int(2 * rx_e * k + ATTR_W + 10), int(2 * ry_e * k + ATTR_H + 10)) if attrs[e]
                  else (ENT_W + 20, ENT_H + 20))
    for i, (nome, _, a, ca, b, cb, ra) in enumerate(rels):
        attrs[f'r{i}'] = ra
        nos[f'r{i}'] = (int(2 * rx_r + ATTR_W + 10), int(2 * ry_r + ATTR_H + 10)) if ra else (REL_W + 10, REL_H + 10)
    grafo = []
    for i, r in enumerate(rels):
        grafo += [(r[2], f'r{i}', 2), (f'r{i}', r[4], 2)]
    pos, _ = layout(nos, grafo, rankdir='TB' if tema else 'LR', nodesep=0.25, ranksep=0.35)
    centro = {n: (pos[n][0] + w / 2, pos[n][1] + h / 2) for n, (w, h) in nos.items()}
    for i, r in enumerate(rels):
        if r[2] == r[4]:   # auto-relacionamento: losango à direita da entidade, fora do anel de atributos
            cx, cy = centro[r[2]]
            centro[f'r{i}'] = (cx + (rx_e * escala[r[2]] + ATTR_W / 2 if attrs[r[2]] else ENT_W / 2) + REL_W / 2 + 40, cy)
    blocos, linhas = [], []
    vizinhos = {n: [] for n in nos}
    for i, r in enumerate(rels):
        vizinhos[r[2]].append(f'r{i}')
        vizinhos[r[4]].append(f'r{i}')
        vizinhos[f'r{i}'] += [r[2], r[4]]

    def angulos(n):
        cx, cy = centro[n]
        return [math.degrees(math.atan2(-(centro[v][1] - cy), centro[v][0] - cx)) % 360 for v in vizinhos[n] if v != n]

    for e in [e for e in MER_ENTIDADES if e in usadas]:
        cx, cy = centro[e]
        blocos.append({'id': e, 't': 'entity', 'txt': e, 'x': round(cx - ENT_W / 2), 'y': round(cy - ENT_H / 2),
                       'w': ENT_W, 'h': ENT_H, 'viz': e not in no_tema})
        larg = [_larg_attr(at) for at in attrs[e]]
        for at, w, (ax, ay) in zip(attrs[e], larg, _anel(cx, cy, larg, angulos(e), rx_e * escala[e], ry_e * escala[e],
                                                          (ENT_W, ENT_H))):
            blocos.append({'id': f'{e}.{at}', 't': 'attribute', 'txt': at, 'x': ax - w // 2, 'y': ay - ATTR_H // 2,
                           'w': w, 'h': ATTR_H, 'chave': at == 'id'})
            linhas.append({'de': f'{e}.{at}', 'para': e})
    for i, (nome, _, a, ca, b, cb, ra) in enumerate(rels):
        rid = f'r{i}'
        cx, cy = centro[rid]
        blocos.append({'id': rid, 't': 'relationship', 'txt': nome, 'x': round(cx - REL_W / 2),
                       'y': round(cy - REL_H / 2), 'w': REL_W, 'h': REL_H})
        larg = [_larg_attr(at) for at in ra]
        for at, w, (ax, ay) in zip(ra, larg, _anel(cx, cy, larg, angulos(rid), rx_r, ry_r, (REL_W, REL_H))):
            blocos.append({'id': f'{rid}.{at}', 't': 'attribute', 'txt': at, 'x': ax - w // 2,
                           'y': ay - ATTR_H // 2, 'w': w, 'h': ATTR_H})
            linhas.append({'de': f'{rid}.{at}', 'para': rid})
        if a == b:   # auto-relacionamento: duas linhas, uma em cima e outra embaixo do losango
            linhas.append({'de': a, 'para': rid, 'card': ca, 'pa': [1, 0.3], 'pb': [0.5, 0]})
            linhas.append({'de': b, 'para': rid, 'card': cb, 'pa': [1, 0.7], 'pb': [0.5, 1]})
        else:
            linhas.append({'de': a, 'para': rid, 'card': ca})
            linhas.append({'de': b, 'para': rid, 'card': cb})
    return {'blocos': blocos, 'linhas': linhas}


# ─── Casos de uso ───────────────────────────────────────────────────────────────────────────────
# Atores: id -> nome. Externos (sistemas) ficam à direita.
ATORES = {
    'visitante': 'Visitante', 'cliente': 'Cliente', 'adm': 'Administradora', 'prof': 'Profissional',
    'tempo': 'Agendador\n(tempo)', 'sms': 'Serviço de SMS', 'wa': 'WhatsApp\n(Meta)', 'mail': 'Serviço de\ne-mail',
}
EXTERNOS = {'sms', 'wa', 'mail'}
# Página: (título, fronteira, atores, casos {id: (nome, pontos de extensão)}, relações)
# Relações: ('assoc', ator, caso) | ('inc', base, incluído) | ('ext', extensão, base, condição) | ('ger', filho, pai)
CASOS_DE_USO = OrderedDict([
    ('site', ('Site público e agendamento online', 'Site público', [
        'visitante', 'sms'], OrderedDict([
            ('consultar', ('Consultar serviços,\npreços e promoções', [])),
            ('equipe', ('Conhecer a equipe\ne os depoimentos', [])),
            ('agendar', ('Agendar atendimento\nonline', ['sem horário livre', 'procedimento com ficha',
                                                       'procedimento com termo', 'comunicações'])),
            ('escolher', ('Escolher procedimento,\nprofissional e horário', [])),
            ('otp', ('Verificar o celular\npor código (SMS)', [])),
            ('politica', ('Aceitar a Política\nde Privacidade', [])),
            ('ficha', ('Responder a ficha\nde anamnese', [])),
            ('termo', ('Aceitar o termo\ndo procedimento', [])),
            ('comunic', ('Autorizar lembretes\ne promoções', [])),
            ('espera', ('Entrar na lista\nde espera', [])),
            ('copia', ('Pedir cópia dos\nmeus dados (LGPD)', [])),
            ('descad', ('Descadastrar-se\ndas promoções', [])),
        ]), [
            ('assoc', 'visitante', 'consultar'), ('assoc', 'visitante', 'equipe'), ('assoc', 'visitante', 'agendar'),
            ('assoc', 'visitante', 'copia'), ('assoc', 'visitante', 'descad'),
            ('inc', 'agendar', 'escolher'), ('inc', 'agendar', 'otp'), ('inc', 'agendar', 'politica'),
            ('ext', 'ficha', 'agendar', 'procedimento com ficha'), ('ext', 'termo', 'agendar', 'procedimento com termo'),
            ('ext', 'comunic', 'agendar', 'cliente escolhe'), ('ext', 'espera', 'agendar', 'sem horário livre'),
            ('inc', 'copia', 'otp'), ('assoc', 'otp', 'sms'),
        ])),
    ('cliente', ('Área da cliente', 'Área da cliente', ['cliente', 'sms'], OrderedDict([
        ('entrar', ('Entrar na área da\ncliente pelo celular', [])),
        ('otp', ('Verificar o celular\npor código (SMS)', [])),
        ('ver', ('Consultar meus\nagendamentos', ['agendamento futuro', 'pacote ativo'])),
        ('cancelar', ('Cancelar\nagendamento', [])),
        ('remarcar', ('Remarcar\nagendamento', [])),
        ('saldo', ('Ver saldo de\nsessões do pacote', [])),
        ('credito', ('Ver crédito e código\nde indicação', [])),
        ('prefs', ('Gerir preferências\nde comunicação', [])),
        ('revogar', ('Revogar consentimento\nde dados de saúde', [])),
        ('direitos', ('Pedir correção ou\nexclusão dos dados', [])),
        ('baixar', ('Baixar meus dados', [])),
        ('confirmar', ('Confirmar presença\npelo lembrete', [])),
        ('nps', ('Avaliar o\natendimento (NPS)', ['pesquisa ativa'])),
        ('pesquisa', ('Responder pesquisa\npós-atendimento', [])),
    ]), [
        ('inc', 'entrar', 'otp'), ('assoc', 'otp', 'sms'),
        ('assoc', 'cliente', 'ver'), ('inc', 'ver', 'entrar'),
        ('ext', 'cancelar', 'ver', 'agendamento futuro'), ('ext', 'remarcar', 'ver', 'até 24 h antes'),
        ('ext', 'saldo', 'ver', 'pacote ativo'),
        ('assoc', 'cliente', 'credito'), ('inc', 'credito', 'entrar'),
        ('assoc', 'cliente', 'prefs'), ('inc', 'prefs', 'entrar'),
        ('assoc', 'cliente', 'revogar'), ('inc', 'revogar', 'entrar'),
        ('assoc', 'cliente', 'direitos'), ('inc', 'direitos', 'entrar'),
        ('assoc', 'cliente', 'baixar'), ('inc', 'baixar', 'entrar'),
        ('assoc', 'cliente', 'confirmar'), ('assoc', 'cliente', 'nps'),
        ('ext', 'pesquisa', 'nps', 'pesquisa ativa'),
    ])),
    ('agenda', ('Painel: agenda', 'Painel da clínica', ['adm', 'wa', 'mail'], OrderedDict([
        ('login', ('Entrar no painel\n(senha + 2FA)', [])),
        ('visao', ('Ver a visão\ngeral do dia', [])),
        ('aprovar', ('Aprovar ou recusar\npedido online', [])),
        ('recepcao', ('Agendar pela\nrecepção', ['cliente sem cadastro'])),
        ('cadastrar', ('Cadastrar cliente\nnova', [])),
        ('validar', ('Validar horário\ndisponível', [])),
        ('realizado', ('Marcar atendimento\nrealizado', ['pacote', 'retorno', 'termo pendente'])),
        ('comissao', ('Gerar comissão', [])),
        ('sessao', ('Debitar sessão\ndo pacote', [])),
        ('retorno', ('Sugerir retorno', [])),
        ('semtermo', ('Confirmar sem\ntermo aceito', [])),
        ('falta', ('Marcar falta', [])),
        ('cancelar', ('Cancelar\natendimento', ['há interessados'])),
        ('avisar', ('Avisar a lista\nde espera', [])),
        ('mover', ('Remarcar no\ncalendário', [])),
        ('notificar', ('Notificar a cliente', [])),
        ('bloquear', ('Bloquear horários\ne registrar folgas', [])),
    ]), [
        ('assoc', 'adm', 'visao'), ('assoc', 'adm', 'aprovar'), ('assoc', 'adm', 'recepcao'),
        ('assoc', 'adm', 'realizado'), ('assoc', 'adm', 'falta'), ('assoc', 'adm', 'cancelar'),
        ('assoc', 'adm', 'mover'), ('assoc', 'adm', 'bloquear'),
        ('inc', 'visao', 'login'), ('inc', 'aprovar', 'notificar'), ('inc', 'recepcao', 'validar'),
        ('ext', 'cadastrar', 'recepcao', 'cliente sem cadastro'),
        ('inc', 'realizado', 'comissao'), ('ext', 'sessao', 'realizado', 'atendimento de pacote'),
        ('ext', 'retorno', 'realizado', 'procedimento com retorno'),
        ('ext', 'semtermo', 'realizado', 'termo pendente'),
        ('inc', 'cancelar', 'notificar'), ('ext', 'avisar', 'cancelar', 'há interessados no dia'),
        ('inc', 'mover', 'validar'), ('inc', 'mover', 'notificar'),
        ('assoc', 'notificar', 'wa'), ('assoc', 'notificar', 'mail'), ('assoc', 'avisar', 'wa'),
    ])),
    ('gestao', ('Painel: cadastros e prontuário', 'Painel da clínica', ['adm', 'prof', 'mail'], OrderedDict([
        ('procs', ('Manter procedimentos\ne preços', ['preço por profissional'])),
        ('precoprof', ('Definir preço\npor profissional', [])),
        ('profs', ('Manter profissionais\ne expediente', [])),
        ('promos', ('Manter promoções', ['promoção ativa'])),
        ('campanha', ('Disparar campanha\npor e-mail', [])),
        ('pacotes', ('Manter pacotes', [])),
        ('ficha', ('Consultar a ficha\nda cliente', ['edição', 'sessão realizada'])),
        ('prontuario', ('Editar o prontuário\n(guarda a versão anterior)', [])),
        ('anotar', ('Anotar a sessão', [])),
        ('formularios', ('Gerir formulários\nde anamnese', [])),
        ('termos', ('Publicar nova\nversão de termo', [])),
        ('consent', ('Registrar\nconsentimento', [])),
    ]), [
        ('assoc', 'adm', 'procs'), ('ext', 'precoprof', 'procs', 'preço por profissional'),
        ('assoc', 'adm', 'profs'), ('assoc', 'adm', 'promos'), ('ext', 'campanha', 'promos', 'promoção ativa'),
        ('assoc', 'campanha', 'mail'), ('assoc', 'adm', 'pacotes'),
        ('assoc', 'adm', 'ficha'), ('assoc', 'prof', 'ficha'),
        ('ext', 'prontuario', 'ficha', 'edição'), ('ext', 'anotar', 'ficha', 'sessão realizada'),
        ('assoc', 'adm', 'formularios'), ('assoc', 'adm', 'termos'), ('assoc', 'adm', 'consent'),
    ])),
    ('financeiro', ('Painel: financeiro e administração', 'Painel da clínica', ['adm'], OrderedDict([
        ('vender', ('Vender pacote', ['cancelamento'])),
        ('cancelarcompra', ('Cancelar compra\ne reembolsar', [])),
        ('prorrogar', ('Prorrogar validade\ndo pacote', [])),
        ('fat', ('Consultar faturamento\ne indicadores', ['exportação'])),
        ('exportar', ('Exportar relatório\n(Excel)', [])),
        ('repassar', ('Repassar comissões', [])),
        ('recibo', ('Emitir recibo\ndo repasse', [])),
        ('regras', ('Manter regras\nde comissão', [])),
        ('npsacomp', ('Acompanhar o NPS', ['cliente autorizou'])),
        ('depo', ('Publicar depoimento', [])),
        ('usuarios', ('Gerir usuários e 2FA', [])),
        ('config', ('Ajustar regras e\nidentidade visual', [])),
        ('auditoria', ('Consultar a auditoria', [])),
    ]), [
        ('assoc', 'adm', 'vender'), ('ext', 'cancelarcompra', 'vender', 'cancelamento'),
        ('assoc', 'adm', 'prorrogar'), ('assoc', 'adm', 'fat'), ('ext', 'exportar', 'fat', 'exportação'),
        ('assoc', 'adm', 'repassar'), ('inc', 'repassar', 'recibo'), ('assoc', 'adm', 'regras'),
        ('assoc', 'adm', 'npsacomp'), ('ext', 'depo', 'npsacomp', 'cliente autorizou'),
        ('assoc', 'adm', 'usuarios'), ('assoc', 'adm', 'config'), ('assoc', 'adm', 'auditoria'),
    ])),
    ('portal', ('Portal da profissional', 'Portal da profissional', ['prof', 'wa', 'mail'], OrderedDict([
        ('login', ('Entrar no portal\n(senha + 2FA)', [])),
        ('agenda', ('Ver a própria agenda', ['cliente com alerta'])),
        ('alertas', ('Ver alertas de saúde\ne ficha pendente', [])),
        ('aprovar', ('Aprovar ou recusar\nos próprios pedidos', [])),
        ('notificar', ('Notificar a cliente', [])),
        ('realizado', ('Marcar atendimento\nrealizado', [])),
        ('comissao', ('Gerar comissão', [])),
        ('anotar', ('Anotar a sessão', [])),
        ('ics', ('Assinar a agenda\nno celular (ICS)', [])),
        ('push', ('Receber avisos\nno celular (push)', [])),
        ('minhas', ('Consultar minhas\ncomissões', [])),
    ]), [
        ('assoc', 'prof', 'agenda'), ('inc', 'agenda', 'login'), ('ext', 'alertas', 'agenda', 'cliente com alerta'),
        ('assoc', 'prof', 'aprovar'), ('inc', 'aprovar', 'notificar'), ('assoc', 'prof', 'realizado'),
        ('inc', 'realizado', 'comissao'), ('assoc', 'prof', 'anotar'), ('assoc', 'prof', 'ics'),
        ('assoc', 'prof', 'push'), ('assoc', 'prof', 'minhas'), ('inc', 'minhas', 'login'),
        ('assoc', 'notificar', 'wa'), ('assoc', 'notificar', 'mail'),
    ])),
    ('rotinas', ('Rotinas automáticas', 'Rotinas automáticas', ['tempo', 'wa', 'mail'], OrderedDict([
        ('lembrete', ('Enviar lembrete\nda véspera', [])),
        ('canal', ('Escolher o canal\n(WhatsApp ou e-mail)', [])),
        ('nps', ('Enviar pesquisa NPS', ['nota baixa'])),
        ('detrator', ('Alertar a clínica\nsobre nota baixa', [])),
        ('fila', ('Reenviar e-mails\ncom falha', [])),
        ('vencer', ('Vencer pedidos\nnão aprovados', [])),
        ('pacote', ('Avisar e expirar\npacotes vencidos', [])),
        ('aniversario', ('Enviar felicitação\nde aniversário', [])),
        ('lgpd', ('Anonimizar clientes\ninativos (LGPD)', [])),
        ('desfecho', ('Avisar atendimentos\nsem desfecho', [])),
        ('respostas', ('Receber respostas\ndo WhatsApp', [])),
    ]), [
        ('assoc', 'tempo', 'lembrete'), ('inc', 'lembrete', 'canal'), ('assoc', 'tempo', 'nps'),
        ('ext', 'detrator', 'nps', 'nota até 6'), ('assoc', 'tempo', 'fila'), ('assoc', 'tempo', 'vencer'),
        ('assoc', 'tempo', 'pacote'), ('assoc', 'tempo', 'aniversario'), ('assoc', 'tempo', 'lgpd'),
        ('assoc', 'tempo', 'desfecho'), ('assoc', 'canal', 'wa'), ('assoc', 'canal', 'mail'),
        ('assoc', 'fila', 'mail'), ('assoc', 'wa', 'respostas'),
    ])),
])
UC_W, UC_H, UCX_W, ATOR_W, ATOR_H = 250, 100, 330, 70, 110


def _alt_caso(pontos):
    """Elipse com compartimento de pontos de extensão cresce uma linha por ponto."""
    return 136 + 28 * len(pontos) if pontos else UC_H


def _larg_caso(pontos):
    return UCX_W if pontos else UC_W


def pagina_casos(chaves):
    """Uma página com as áreas `chaves`; várias áreas = diagrama completo (uma fronteira por área)."""
    nos, grafo, clusters, blocos, linhas = {}, [], OrderedDict(), [], []
    atores = []
    for k in chaves:
        for a in CASOS_DE_USO[k][2]:
            if a not in atores:
                atores.append(a)
    for a in atores:
        nos['A_' + a] = (ATOR_W + 40, ATOR_H)
    for k in chaves:
        _, fronteira, _, casos, rels = CASOS_DE_USO[k]
        membros = []
        for cid, (nome, pontos) in casos.items():
            nid = f'{k}.{cid}'
            nos[nid] = (_larg_caso(pontos), _alt_caso(pontos))
            membros.append(nid)
        clusters[k] = (membros, f', label="{fronteira}"')
        for r in rels:
            if r[0] == 'assoc':
                a, b = r[1], r[2]
                na = 'A_' + a if a in ATORES else f'{k}.{a}'
                nb = 'A_' + b if b in ATORES else f'{k}.{b}'
                # ator externo fica à direita do caso, mesmo quando é ele quem inicia
                grafo.append((nb, na, 2) if a in EXTERNOS else (na, nb, 2))
            elif r[0] == 'inc':
                grafo.append((f'{k}.{r[1]}', f'{k}.{r[2]}', 1))
            elif r[0] == 'ext':
                grafo.append((f'{k}.{r[2]}', f'{k}.{r[1]}', 1))
    # externos à direita: invertidos no grafo (caso -> externo já está nesse sentido)
    pos, caixas = layout(nos, grafo, rankdir='LR', nodesep=0.55, ranksep=3.6, clusters=clusters)
    for k, (x, y, w, h) in caixas.items():
        blocos.append({'id': 'F_' + k, 't': 'systemBoundary', 'txt': CASOS_DE_USO[k][1], 'x': x, 'y': y - 30,
                       'w': w, 'h': h + 30})
    for a in atores:
        x, y = pos['A_' + a]
        # ator mais afastado da fronteira: as associações ficam mais deitadas e não cortam as elipses vizinhas
        x += 150 if a in EXTERNOS else -250
        blocos.append({'id': 'A_' + a, 't': 'actor', 'txt': ATORES[a], 'x': x + 20, 'y': y, 'w': ATOR_W, 'h': ATOR_H})
    for k in chaves:
        _, _, _, casos, rels = CASOS_DE_USO[k]
        for cid, (nome, pontos) in casos.items():
            nid = f'{k}.{cid}'
            x, y = pos[nid]
            blocos.append({'id': nid, 't': 'useCaseExtended' if pontos else 'useCase', 'txt': nome,
                           'pontos': pontos, 'x': x, 'y': y, 'w': _larg_caso(pontos), 'h': _alt_caso(pontos)})
        for r in rels:
            if r[0] == 'assoc':
                a, b = r[1], r[2]
                linhas.append({'de': 'A_' + a if a in ATORES else f'{k}.{a}', 'para': 'A_' + b if b in ATORES else f'{k}.{b}',
                               'tipo': 'association'})
            elif r[0] == 'inc':
                linhas.append({'de': f'{k}.{r[1]}', 'para': f'{k}.{r[2]}', 'tipo': 'dependency', 'rot': '«include»'})
            elif r[0] == 'ext':
                linhas.append({'de': f'{k}.{r[1]}', 'para': f'{k}.{r[2]}', 'tipo': 'dependency',
                               'rot': f'«extend»\n[{r[3]}]'})
            elif r[0] == 'ger':
                linhas.append({'de': f'A_{r[1]}', 'para': f'A_{r[2]}', 'tipo': 'generalization'})
    return {'blocos': blocos, 'linhas': linhas}


# ─── Saída ──────────────────────────────────────────────────────────────────────────────────────


def paginas():
    """[(documento, chave, título da página, dados)] na ordem das páginas de cada documento."""
    tabelas = modelo()
    todas = [t for _, (_, ts) in TEMAS.items() for t in ts]
    out = [('mer', 'completo', 'MER completo', pagina_mer(None))]
    out += [('mer', tema, titulo, pagina_mer(tema)) for tema, (titulo, _) in TEMAS.items()]
    out += [('der', 'completo', 'DER completo', pagina_der(tabelas, todas))]
    out += [('der', tema, titulo, pagina_der(tabelas, ts)) for tema, (titulo, ts) in TEMAS.items()]
    out += [('classes', 'completo', 'Classes: todas', pagina_classes(tabelas, todas, referencias=False))]
    out += [('classes', tema, titulo, pagina_classes(tabelas, ts)) for tema, (titulo, ts) in TEMAS.items()]
    out += [('casos', 'completo', 'Casos de uso: todos', pagina_casos(list(CASOS_DE_USO)))]
    out += [('casos', k, v[0], pagina_casos([k])) for k, v in CASOS_DE_USO.items()]
    return out


# ─── Formato compacto (o que vai para o Lucid) ──────────────────────────────────────────────────
# Cada lote = um motor por tipo de diagrama + a lista de páginas em listas curtas (o texto vai uma vez só).

def _idx(blocos):
    return {b['id']: i for i, b in enumerate(blocos)}


def compacto_mer(titulo, d):
    ix = _idx(d['blocos'])
    cod = {'entity': 'E', 'relationship': 'R', 'attribute': 'A'}
    b = []
    for x in d['blocos']:
        k = cod[x['t']]
        if k == 'E' and x.get('viz'):
            k = 'V'
        if k == 'A' and x.get('chave'):
            k = 'K'
        b.append([k, x['txt'], x['x'], x['y']] + ([x['w']] if k in 'AK' and x['w'] != ATTR_W else []))
    linhas = []
    for ln in d['linhas']:
        item = [ix[ln['de']], ix[ln['para']], ln.get('card', '')]
        if ln.get('pa'):
            item += ln['pa'] + ln['pb']
        linhas.append(item)
    return {'t': titulo, 'b': b, 'l': linhas}


MOTOR_MER = r"""
const U = skills.uml, T = skills.text, B = skills.basecanvasediting;
const TAM = {E: [190, 70], V: [190, 70], R: [170, 96], A: [150, 50], K: [150, 50]};
const TIPO = {E: 'entity', V: 'entity', R: 'relationship', A: 'attribute', K: 'attribute'};
const out = [];
for (const D of PAGINAS) {
  const pg = B.getPages().find(p => p.title === D.t);
  if (!pg || !B.navigateToPage(pg.id)) throw new Error('página ' + D.t);
  const ids = [];
  for (const [k, txt, x, y, larg] of D.b) {
    const [w0, h] = TAM[k], w = larg || w0;
    const s = U.addShape({type: TIPO[k], label: txt, location: {x, y}, width: w, height: h,
                          fillColor: k === 'V' ? '#f2f2f2' : '#ffffff', properties: k === 'V' ? {StrokeStyle: 'dashed'} : {}});
    ids.push(s.id);
    if (k === 'K') await T.setItemTextStyle({itemId: s.id, styles: [{style: 'underline', value: 'true'}]});
  }
  for (const l of D.l) {
    const de = {kind: 'block', blockId: ids[l[0]]}, para = {kind: 'block', blockId: ids[l[1]]};
    if (l.length > 3) { de.connection = {x: l[3], y: l[4]}; para.connection = {x: l[5], y: l[6]}; }
    B.createLine({from: de, to: para, label: l[2] || undefined, shape: 'diagonal',
                  style: {lineColor: '#333333', lineWidth: 1, endpoint1: 'None', endpoint2: 'None'}});
  }
  out.push([D.t, ids.length, D.l.length]);
}
return out;
"""

_CARD = {'exactlyOne': '1', 'zeroOrOne': '0', 'zeroOrMany': 'M'}


def compacto_der(titulo, d):
    ix = {e['key']: i for i, e in enumerate(d['entities'])}
    e = [[x['name'], x['location']['x'], x['location']['y'], int(x['externa']),
          [[f['name'], f['type'], f['key'], int(f['nullable'])] for f in x['fields']]] for x in d['entities']]
    r = [[ix[x['primaryEntity']], ix[x['foreignEntity']], x['foreignField'], _CARD[x['primaryCardinality']],
          _CARD[x['foreignCardinality']]] for x in d['relationships']]
    return {'t': titulo, 'e': e, 'r': r}


MOTOR_DER = r"""
const E = skills.erds, B = skills.basecanvasediting;
const C = {'1': 'exactlyOne', '0': 'zeroOrOne', 'M': 'zeroOrMany'};
const out = [];
for (const D of PAGINAS) {
  const pg = B.getPages().find(p => p.title === D.t);
  if (!pg || !B.navigateToPage(pg.id)) throw new Error('página ' + D.t);
  const ents = D.e.map((e, i) => ({key: 'e' + i, name: e[0], location: {x: e[1], y: e[2]}, variant: 'keyFieldType',
    fields: e[4].map(f => ({name: f[0], type: f[1], key: f[2] || undefined, nullable: !!f[3]}))}));
  const rels = D.r.map(r => ({primaryEntity: 'e' + r[0], foreignEntity: 'e' + r[1], primaryField: 'id',
    foreignField: r[2], primaryCardinality: C[r[3]], foreignCardinality: C[r[4]]}));
  const res = E.createSchema({entities: ents, relationships: rels, variant: 'keyFieldType'});
  // coluna do tipo larga o bastante para "numeric(10,2) NOT NULL" caber numa linha (a criação ignora a largura)
  D.e.forEach((e, i) => E.update(res.entityIdsByKey['e' + i], e[3] ? {columnWidths: [55, 215, 225],
    headerShading: {enabled: true, color: '#d9d9d9'}} : {columnWidths: [55, 215, 225]}));
  await B.rerouteLines({blockIds: Object.values(res.entityIdsByKey), lineIds: res.relationships.map(r => r.lineId)});
  out.push([D.t, res.entities.length, res.relationships.length]);
}
return out;
"""

_REL_CLASSE = {'composition': 'C', 'directedAssociation': 'A', 'dependency': 'D'}


def compacto_classes(titulo, d):
    ix = _idx(d['nos'])
    n = []
    for x in d['nos']:
        if x['k'] == 'R':
            n.append(['R', x['nome'], x['x'], x['y'], x['w'], x['h']])
        else:
            attrs = [a[2:] if a.startswith('- ') else a for a in x['attrs']]
            estaticas = [i for i, o in enumerate(x.get('ops', [])) if o in x.get('estaticas', [])]
            n.append([x['k'], x['nome'], x['x'], x['y'], x['w'], x['h'], attrs, x.get('ops', []), estaticas])
    a = [[ix[y['de']], ix[y['para']], _REL_CLASSE[y['tipo']], y['m1'], y['m2'], y['rot']] for y in d['arestas']]
    return {'t': titulo, 'n': n, 'a': a}


MOTOR_CLASSES = r"""
const U = skills.uml, T = skills.text, B = skills.basecanvasediting;
const REL = {C: 'composition', A: 'directedAssociation', D: 'dependency'};
const out = [];
for (const D of PAGINAS) {
  const pg = B.getPages().find(p => p.title === D.t);
  if (!pg || !B.navigateToPage(pg.id)) throw new Error('página ' + D.t);
  const ids = [];
  for (const [k, nome, x, y, w, h, attrs, ops, estaticas] of D.n) {
    if (k === 'R') {
      ids.push(U.addShape({type: 'classSimple', label: nome, location: {x, y}, width: w, height: h,
                           fillColor: '#f2f2f2', properties: {StrokeStyle: 'dashed'}}).id);
      continue;
    }
    const atributos = k === 'E' ? attrs : attrs.map(a => a.startsWith('+') ? a : '- ' + a);
    const s = U.addShape({type: 'class', location: {x, y}, width: w, height: h, fillColor: '#ffffff',
                          compartments: {name: nome, attributes: atributos, operations: ops,
                                         compartmentHeightSplit: k === 'E' ? 1 : 0.5}});
    ids.push(s.id);
    for (const i of estaticas) {
      await T.setPartialTextStyle({itemId: s.id, textAreaName: 'Text2', substring: ops[i],
                                   styles: [{style: 'underline', value: 'true'}]});
    }
  }
  const LACOS = [[{x: 1, y: 0.18}, {x: 0.72, y: 0}], [{x: 1, y: 0.82}, {x: 0.96, y: 1}]];
  const nLacos = {}, linhas = [], lacos = [];
  for (const a of D.a) {
    if (a[0] !== a[1]) {
      linhas.push(U.addRelationship({from: ids[a[0]], to: ids[a[1]], type: REL[a[2]], label: a[5] || undefined,
        fromMultiplicity: a[3] || undefined, toMultiplicity: a[4] || undefined, shape: 'elbow'}));
      continue;
    }
    const n = nLacos[a[0]] = (nLacos[a[0]] || 0) + 1, [p1, p2] = LACOS[(n - 1) % 2];
    const id = B.createLine({from: {kind: 'block', blockId: ids[a[0]], connection: p1},
                             to: {kind: 'block', blockId: ids[a[0]], connection: p2}, shape: 'elbow'}).lineId;
    U.setRelationshipType(id, REL[a[2]]);
    U.setMultiplicity(id, 'from', a[3]);
    U.setMultiplicity(id, 'to', a[4]);
    U.setRelationshipLabel(id, a[5]);
    lacos.push(id);
  }
  await B.rerouteLines({blockIds: ids, lineIds: linhas});
  linhas.push(...lacos);
  out.push([D.t, ids.length, linhas.length]);
}
return out;
"""

_COD_CASO = {'systemBoundary': 'F', 'actor': 'A', 'useCase': 'U', 'useCaseExtended': 'X'}


def compacto_casos(titulo, d):
    ix = _idx(d['blocos'])
    b = [[_COD_CASO[x['t']], x['txt'], x['x'], x['y'], x['w'], x['h']] + ([x['pontos']] if x.get('pontos') else [])
         for x in d['blocos']]
    linhas = []
    for ln in d['linhas']:
        kind = {'association': 'a', 'generalization': 'g'}.get(ln['tipo'])
        rot = ln.get('rot', '')
        if rot == '«include»':
            kind, cond = 'i', ''
        elif rot.startswith('«extend»'):
            kind, cond = 'e', rot.split('[', 1)[1].rstrip(']')
        else:
            cond = ''
        linhas.append([ix[ln['de']], ix[ln['para']], kind] + ([cond] if cond else []))
    return {'t': titulo, 'b': b, 'l': linhas}


MOTOR_CASOS = r"""
const U = skills.uml, B = skills.basecanvasediting;
const TIPO = {F: 'systemBoundary', A: 'actor', U: 'useCase', X: 'useCaseExtended'};
const out = [];
for (const D of PAGINAS) {
  const pg = B.getPages().find(p => p.title === D.t);
  if (!pg || !B.navigateToPage(pg.id)) throw new Error('página ' + D.t);
  const velhos = [...U.list().map(s => s.id), ...U.listRelationships().map(r => r.id)];
  if (velhos.length) B.deleteItem({itemIds: [...new Set(velhos)]});
  const ids = [];
  for (const [k, txt, x, y, w, h, pontos] of D.b) {
    const s = U.addShape({type: TIPO[k], label: txt, location: {x, y}, width: w, height: h,
                          fillColor: k === 'F' ? '#ffffff00' : '#ffffff'});
    ids.push(s.id);
    // o título "Extension Points" do compartimento é fixo no Lucid (palavra-chave da notação UML)
    if (pontos) U.setText(s.id, {ExtensionPoints: pontos.join('\n')});
  }
  for (const [i, j, kind, cond] of D.l) {
    const tipo = {a: 'association', g: 'generalization', i: 'dependency', e: 'dependency'}[kind];
    const rot = kind === 'i' ? '«include»' : kind === 'e' ? '«extend»\n[' + cond + ']' : undefined;
    U.addRelationship({from: ids[i], to: ids[j], type: tipo, label: rot, shape: 'diagonal'});
  }
  out.push([D.t, ids.length, D.l.length]);
}
return out;
"""

COMPACTO = {'mer': (compacto_mer, MOTOR_MER), 'der': (compacto_der, MOTOR_DER),
            'classes': (compacto_classes, MOTOR_CLASSES), 'casos': (compacto_casos, MOTOR_CASOS)}


if __name__ == '__main__':
    destino = Path(sys.argv[1] if len(sys.argv) > 1 else 'saida_lucid')
    limite = int(sys.argv[2]) * 1024 if len(sys.argv) > 2 else 20 * 1024
    por_doc = OrderedDict()
    for doc, _chave, titulo, dados in paginas():
        compactar, _motor = COMPACTO[doc]
        por_doc.setdefault(doc, []).append(compactar(titulo, dados))
    indice = OrderedDict()
    for doc, pags in por_doc.items():
        pasta = destino / doc
        pasta.mkdir(parents=True, exist_ok=True)
        motor = COMPACTO[doc][1].strip()
        lotes, atual = [], []
        for pg in pags:
            js = json.dumps(pg, ensure_ascii=False, separators=(',', ':'))
            if atual and sum(len(x) for x in atual) + len(js) > limite:
                lotes.append(atual)
                atual = []
            atual.append(js)
        lotes.append(atual)
        for n, lote in enumerate(lotes):
            corpo = 'const PAGINAS = [' + ',\n'.join(lote) + '];\n' + motor + '\n'
            (pasta / f'lote{n:02d}.js').write_text(corpo, encoding='utf-8')
            indice.setdefault(doc, []).append((f'lote{n:02d}.js', len(lote), len(corpo) // 1024))
        (pasta / 'paginas.json').write_text(json.dumps([pg['t'] for pg in pags], ensure_ascii=False), encoding='utf-8')
    for doc, ls in indice.items():
        print(doc, ls)
