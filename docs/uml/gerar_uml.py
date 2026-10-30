"""Diagramas UML do Shiva Zen: gera ER e classes a partir do codigo e renderiza tudo (SVG + PNG).

    python docs/uml/gerar_uml.py er        # fontes/08-dados/*.puml (modelo fisico) a partir dos models
    python docs/uml/gerar_uml.py classes   # fontes/02-classes/*.puml (dominio + camada de servicos)
    python docs/uml/gerar_uml.py render    # todo fontes/**/*.puml -> svg/ e png/ (PlantUML)
    python docs/uml/gerar_uml.py           # os tres

Rode com o Python do projeto (.venv): o `er` le os models (tipos reais do Postgres, PK/FK/UK,
nulidade). O `render` usa o PlantUML (env PLANTUML = caminho do executavel nativo ou do .jar;
sem ela, `plantuml` do PATH) e o Graphviz (env GRAPHVIZ_DOT, se nao estiver no PATH).
As fontes ER e de classes sao geradas — nao edite a mao; os demais diagramas (casos de uso,
sequencia, estados, atividades, componentes, implantacao) sao escritos em fontes/.
"""
import ast
import importlib
import inspect
import os
import pkgutil
import re
import shutil
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
UML = Path(__file__).resolve().parent
FONTES = UML / 'fontes'
DADOS = FONTES / '08-dados'
CLASSES = FONTES / '02-classes'

# Partes do modelo fisico (uma imagem legivel por assunto); tabela nova sem parte = erro.
PARTES = OrderedDict([
    ('01-er-agenda-atendimento', ('Agenda e atendimento', [
        'profissional', 'procedimento', 'habilitacao', 'preco', 'promocao', 'agenda_horario',
        'agenda_excecao', 'atendimento', 'lista_espera'])),
    ('02-er-clientes-saude', ('Clientes, saúde e formulários', [
        'cliente', 'prontuario', 'prontuario_versao', 'anotacao_sessao', 'formulario', 'resposta_formulario'])),
    ('03-er-financeiro', ('Pacotes, comissão e carteira', [
        'pacote', 'pacote_item', 'compra_pacote', 'compra_pacote_item', 'consumo_sessao', 'regra_comissao',
        'movimento_comissao', 'carteira', 'movimento_carteira'])),
    ('04-er-comunicacao', ('Comunicação, links de acesso e verificação', [
        'notificacao', 'avaliacao_nps', 'assinatura_push', 'link_acesso', 'codigo_otp', 'execucao_job'])),
    ('05-er-lgpd-seguranca', ('LGPD, termos, auditoria e acesso', [
        'consentimento', 'versao_termo', 'aceite_termo', 'log_auditoria', 'historico_alteracao', 'usuario',
        'configuracao'])),
])


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
    return list(apps.get_app_config('aranha_estetica').get_models()), pg


def _tipo(field, pg):
    tipo = (field.db_type(pg) or '').replace('timestamp with time zone', 'timestamptz')
    return re.sub(r'numeric\((\d+), (\d+)\)', r'numeric(\1,\2)', tipo)


# metadados de linha (padrao-sql §1.2) vao por ultimo, nesta ordem, como no DER do Lucid
METADADOS = ('created_at', 'updated_at', 'deleted_at', 'created_by_id', 'created_by_name')


def _colunas(model):
    def chave(f):
        return METADADOS.index(f.column) + 1 if f.column in METADADOS else 0
    return sorted(model._meta.concrete_fields, key=chave)   # sort estavel: o resto na ordem do model


def _entidade(model, pg, completa=True):
    tabela = model._meta.db_table
    if not completa:
        return f'entity "{tabela}" as {tabela} #F2F2F2 {{\n}}\n'
    linhas = [f'entity "{tabela}" as {tabela} {{']
    pk = model._meta.pk
    linhas.append(f'  * <b>{pk.column}</b> : {_tipo(pk, pg)} <<PK>>')
    linhas.append('  --')
    for f in _colunas(model):
        if f.primary_key:
            continue
        marcas = []
        if f.is_relation:
            marcas.append('FK')
        if f.unique:
            marcas.append('UK')
        obrig = '' if f.null else '* '
        sufixo = f" <<{','.join(marcas)}>>" if marcas else ''
        linhas.append(f'  {obrig}{f.column} : {_tipo(f, pg)}{sufixo}')
    linhas.append('}')
    return '\n'.join(linhas) + '\n'


def _relacoes(models, tabelas):
    out = []
    for m in models:
        filho = m._meta.db_table
        for f in m._meta.concrete_fields:
            if not f.is_relation or f.related_model is None:
                continue
            pai = f.related_model._meta.db_table
            if filho not in tabelas and pai not in tabelas:
                continue
            lado_pai = '|o' if f.null else '||'
            lado_filho = 'o|' if f.one_to_one or f.unique else 'o{'
            out.append(f'{pai} {lado_pai}--{lado_filho} {filho} : {f.column}')
    return out


def gerar_er():
    models, pg = _django()
    por_tabela = {m._meta.db_table: m for m in models}
    sem_parte = sorted(set(por_tabela) - {t for _, ts in PARTES.values() for t in ts})
    if sem_parte:
        raise SystemExit(f'tabelas sem parte em PARTES (docs/uml/gerar_uml.py): {sem_parte}')
    DADOS.mkdir(parents=True, exist_ok=True)
    cabecalho = ("' GERADO por docs/uml/gerar_uml.py a partir dos models — nao edite a mao.\n"
                 "!include ../estilo.iuml\n!include ../estilo-er.iuml\n")

    # visao geral: todas as tabelas, so nomes e ligacoes
    corpo = [f"@startuml 00-er-visao-geral\n{cabecalho}title Modelo de dados — visão geral ({len(models)} tabelas)\n"]
    for nome, (titulo, tabelas) in PARTES.items():
        corpo.append(f'package "{titulo}" {{')
        for t in tabelas:
            corpo.append(f'  entity "{t}" as {t}')
        corpo.append('}')
    corpo += _relacoes(models, set(por_tabela))
    corpo.append('@enduml\n')
    (DADOS / '00-er-visao-geral.puml').write_text('\n'.join(corpo), encoding='utf-8', newline='\n')

    for nome, (titulo, tabelas) in PARTES.items():
        conjunto = set(tabelas)
        corpo = [f'@startuml {nome}\n{cabecalho}title Modelo de dados — {titulo}\n']
        for t in tabelas:
            corpo.append(_entidade(por_tabela[t], pg))
        relacoes = _relacoes(models, conjunto)
        externas = ({r.split()[0] for r in relacoes} | {r.split()[2] for r in relacoes}) - conjunto
        for t in sorted(externas):
            corpo.append(_entidade(por_tabela[t], pg, completa=False))
        corpo += relacoes
        corpo.append('@enduml\n')
        (DADOS / f'{nome}.puml').write_text('\n'.join(corpo), encoding='utf-8', newline='\n')
    print(f'ER: {len(PARTES) + 1} diagramas em {DADOS.relative_to(RAIZ)}')


# ─── Classes do dominio (models) e camada de servicos ─────────────────────

_TIPOS_PY = {
    'AutoField': 'int', 'BigAutoField': 'int', 'SmallAutoField': 'int', 'IntegerField': 'int',
    'BigIntegerField': 'int', 'SmallIntegerField': 'int', 'PositiveIntegerField': 'int',
    'PositiveSmallIntegerField': 'int', 'PositiveBigIntegerField': 'int', 'CharField': 'str',
    'TextField': 'str', 'EmailField': 'str', 'SlugField': 'str', 'URLField': 'str',
    'GenericIPAddressField': 'IP', 'BooleanField': 'bool', 'DecimalField': 'Decimal',
    'FloatField': 'float', 'DateTimeField': 'datetime', 'DateField': 'date', 'TimeField': 'time',
    'DurationField': 'timedelta', 'JSONField': 'json', 'BinaryField': 'bytes', 'UUIDField': 'UUID',
}
_METODOS_DJANGO = {'clean', 'save', 'delete', 'full_clean', 'natural_key', 'get_absolute_url'}


def _enums():
    """{tupla de choices: (nome, classe)} das TextChoices/IntegerChoices declaradas em models/."""
    from django.db import models as dj

    import aranha_estetica.models as pacote
    achados = {}
    for info in pkgutil.iter_modules(pacote.__path__):
        mod = importlib.import_module(f'aranha_estetica.models.{info.name}')
        for nome, obj in vars(mod).items():
            if inspect.isclass(obj) and issubclass(obj, dj.Choices) and obj.__module__ == mod.__name__:
                achados[tuple(obj.choices)] = (nome, obj)
    return achados


def _tipo_py(field):
    nome_classe = type(field).__name__
    if 'Cifrad' in nome_classe:
        return ('json' if 'Json' in nome_classe else 'str') + ' «cifrado»'
    interno = field.get_internal_type()
    return _TIPOS_PY.get(interno, interno)


def _assinatura(func):
    try:
        params = list(inspect.signature(func).parameters.values())
    except (TypeError, ValueError):
        return '()'
    partes, kw = [], False
    for p in params:
        if p.name in ('self', 'cls'):
            continue
        if p.kind == p.KEYWORD_ONLY and not kw:
            partes.append('*')
            kw = True
        if p.kind == p.VAR_POSITIONAL:
            partes.append('*' + p.name)
        elif p.kind == p.VAR_KEYWORD:
            partes.append('**' + p.name)
        else:
            partes.append(p.name)
    texto = ', '.join(partes)
    if len(texto) > 42:
        texto = texto[:40] + '…'
    return f'({texto})'


def _metodos(cls):
    linhas = []
    for nome, obj in cls.__dict__.items():
        if nome.startswith('_') or nome in _METODOS_DJANGO:
            continue
        if nome.startswith('get_') and nome.endswith('_display'):
            continue
        if isinstance(obj, property):
            linhas.append(f'  + /{nome} «property»')
        elif isinstance(obj, (staticmethod, classmethod)):
            linhas.append(f'  + {{static}} {nome}{_assinatura(obj.__func__)}')
        elif inspect.isfunction(obj):
            linhas.append(f'  + {nome}{_assinatura(obj)}')
    return linhas


def _classe(model, enums, usados, completa=True):
    nome = model.__name__
    if not completa:
        return f'class {nome} #F2F2F2\nhide {nome} members\n'
    linhas = [f'class {nome} <<Entity>> {{']
    for f in model._meta.concrete_fields:
        if f.primary_key or f.is_relation:
            continue
        tipo = _tipo_py(f)
        if f.choices:
            chave = tuple(f.choices)
            if chave in enums:
                tipo = enums[chave][0]
                usados[tipo] = enums[chave][1]
        mult = ' [0..1]' if f.null else ''
        linhas.append(f'  + {f.name} : {tipo}{mult}')
    metodos = _metodos(model)
    if metodos:
        linhas.append('  --')
        linhas += metodos
    linhas.append('}')
    qs = type(model._default_manager.get_queryset())
    if qs.__module__.startswith('aranha_estetica') and qs.__name__ != 'QuerySet':
        ops = _metodos(qs)
        if ops:
            linhas.append(f'class {qs.__name__} <<QuerySet>> {{')
            linhas += ops
            linhas.append('}')
            linhas.append(f'{nome} ..> {qs.__name__} : objects')
    return '\n'.join(linhas) + '\n'


def _associacoes(models, nomes):
    out = []
    for m in models:
        for f in m._meta.get_fields():
            if not getattr(f, 'concrete', False) or not f.is_relation or f.related_model is None:
                continue
            alvo = f.related_model.__name__
            if m.__name__ not in nomes and alvo not in nomes:
                continue
            destino = '0..1' if f.null else '1'
            if f.many_to_many:
                out.append(f'{m.__name__} "0..*" -- "0..*" {alvo} : {f.name}')
            elif f.one_to_one or f.unique:
                out.append(f'{m.__name__} "0..1" --> "{destino}" {alvo} : {f.name}')
            else:
                out.append(f'{m.__name__} "0..*" --> "{destino}" {alvo} : {f.name}')
    return out


def _enum_bloco(nome, cls):
    linhas = [f'enum {nome} {{'] + [f'  {valor}' for valor in cls.values] + ['}']
    return '\n'.join(linhas) + '\n'


# Camada de servicos: modulo -> grupo (um diagrama por grupo). Modulo novo sem grupo = erro.
GRUPOS_SERVICOS = OrderedDict([
    ('10-servicos-agenda', ('Serviços — agenda, pedido online e atendimento', [
        'agenda', 'agendamento', 'agendamento_online', 'atendimento', 'disponibilidade', 'retorno',
        'pacotes', 'precos', 'catalogo', 'lista_espera', 'indicadores', 'prova_social'])),
    ('11-servicos-clinico-lgpd', ('Serviços — fichas, prontuário, termos e LGPD', [
        'anamnese', 'alertas', 'prontuario', 'termos', 'consentimentos', 'lgpd', 'portal_cliente'])),
    ('12-servicos-financeiro', ('Serviços — financeiro e relatórios', [
        'comissao', 'fidelidade', 'financeiro', 'relatorios'])),
    ('13-servicos-comunicacao', ('Serviços — comunicação e rotinas', [
        'comunicacao', 'lembretes', 'nps', 'marketing', 'feriados', 'links', 'otp', 'push'])),
])


def _api_servico(caminho):
    arvore = ast.parse(caminho.read_text(encoding='utf-8'))
    ops, classes = [], []
    for no in arvore.body:
        if isinstance(no, ast.FunctionDef) and not no.name.startswith('_'):
            args = [a.arg for a in no.args.args]
            if no.args.kwonlyargs:
                args += ['*'] + [a.arg for a in no.args.kwonlyargs]
            texto = ', '.join(args)
            if len(texto) > 40:
                texto = texto[:38] + '…'
            ops.append(f'  + {no.name}({texto})')
        elif isinstance(no, ast.ClassDef) and not no.name.startswith('_'):
            metodos = [m.name for m in no.body if isinstance(m, ast.FunctionDef) and not m.name.startswith('_')]
            classes.append((no.name, metodos))
    deps = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and no.level == 1:
            if no.module:
                deps.add(no.module.split('.')[0])
            else:
                deps.update(a.name for a in no.names)
    return ops, classes, deps


def gerar_classes():
    models, _pg = _django()
    enums = _enums()
    por_tabela = {m._meta.db_table: m for m in models}
    CLASSES.mkdir(parents=True, exist_ok=True)
    cabecalho = ("' GERADO por docs/uml/gerar_uml.py a partir do codigo — nao edite a mao.\n"
                 "!include ../estilo.iuml\nhide empty members\n")
    for i, (nome, (titulo, tabelas)) in enumerate(PARTES.items(), start=1):
        arquivo = f'{i:02d}-classes-{nome[6:]}'
        da_parte = [por_tabela[t] for t in tabelas]
        nomes = {m.__name__ for m in da_parte}
        usados = {}
        corpo = [f'@startuml {arquivo}\n{cabecalho}title Classes do domínio — {titulo}\n']
        corpo += [_classe(m, enums, usados) for m in da_parte]
        # so as associacoes que SAEM da parte (as que chegam aparecem no diagrama da outra parte)
        assoc = [a for a in _associacoes(models, nomes) if a.split()[0] in nomes]
        externas = {a.split()[3] for a in assoc} - nomes
        corpo += [_classe(m, enums, usados, completa=False) for m in models if m.__name__ in externas]
        corpo += [_enum_bloco(n, c) for n, c in sorted(usados.items())]
        corpo += assoc
        corpo.append('@enduml\n')
        (CLASSES / f'{arquivo}.puml').write_text('\n'.join(corpo), encoding='utf-8', newline='\n')

    pasta = RAIZ / 'aranha_estetica' / 'services'
    modulos = {p.stem: p for p in pasta.glob('*.py') if p.stem != '__init__'}
    sem_grupo = sorted(set(modulos) - {m for _, ms in GRUPOS_SERVICOS.values() for m in ms})
    if sem_grupo:
        raise SystemExit(f'servicos sem grupo em GRUPOS_SERVICOS (docs/uml/gerar_uml.py): {sem_grupo}')
    for nome, (titulo, membros) in GRUPOS_SERVICOS.items():
        presentes = [m for m in membros if m in modulos]
        corpo = [f'@startuml {nome}\n{cabecalho}title {titulo}\n']
        setas, externos = set(), set()
        for mod in presentes:
            ops, classes, deps = _api_servico(modulos[mod])
            corpo.append(f'class "services.{mod}" as {mod} <<módulo>> {{')
            corpo += ops
            corpo.append('}')
            for cls, metodos in classes:
                corpo.append(f'class {cls} <<serviço>> {{')
                corpo += [f'  + {{static}} {m}()' for m in metodos]
                corpo.append('}')
                corpo.append(f'{mod} *-- {cls}')
            for dep in sorted((deps & set(modulos)) - {mod}):
                if dep not in presentes:
                    externos.add(dep)
                setas.add(f'{mod} ..> {dep}')
        for dep in sorted(externos):
            corpo.append(f'class "services.{dep}" as {dep} #F2F2F2\nhide {dep} members')
        corpo += sorted(setas)
        corpo.append('@enduml\n')
        (CLASSES / f'{nome}.puml').write_text('\n'.join(corpo), encoding='utf-8', newline='\n')
    print(f'classes: {len(PARTES) + len(GRUPOS_SERVICOS)} diagramas em {CLASSES.relative_to(RAIZ)}')


def _plantuml():
    caminho = os.environ.get('PLANTUML') or shutil.which('plantuml')
    if not caminho:
        raise SystemExit('PlantUML nao encontrado: defina PLANTUML (executavel nativo ou .jar).')
    return ['java', '-jar', caminho] if caminho.endswith('.jar') else [caminho]


def render():
    cmd = _plantuml()
    fontes = sorted(p for p in FONTES.rglob('*.puml'))
    for formato, extra in (('svg', []), ('png', ['-Sdpi=200'])):
        for pasta in sorted({p.parent for p in fontes}):
            destino = UML / formato / pasta.relative_to(FONTES)
            destino.mkdir(parents=True, exist_ok=True)
            arquivos = [str(p) for p in fontes if p.parent == pasta]
            r = subprocess.run([*cmd, f'-t{formato}', '-charset', 'UTF-8', *extra, '-o', str(destino), *arquivos],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit(f'PlantUML falhou em {pasta.name} ({formato}):\n{r.stdout}\n{r.stderr}')
    print(f'render: {len(fontes)} diagramas -> {UML.relative_to(RAIZ)}/svg e /png')


if __name__ == '__main__':
    acao = sys.argv[1] if len(sys.argv) > 1 else 'tudo'
    if acao in ('er', 'tudo'):
        gerar_er()
    if acao in ('classes', 'tudo'):
        gerar_classes()
    if acao in ('render', 'tudo'):
        render()
