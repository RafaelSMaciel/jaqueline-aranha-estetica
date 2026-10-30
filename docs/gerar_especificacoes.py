# -*- coding: utf-8 -*-
"""
Gera três documentos do TCC (DOCX + PDF em docs/), a partir das mesmas fontes do código:

    ShivaZen-Regras-de-Negocio   catálogo RN01..RNnn + a especificação completa de
                                 docs/REGRAS-DE-NEGOCIO.md + diagramas de apoio
    ShivaZen-Requisitos          requisitos funcionais e não funcionais com prioridade,
                                 verificação e rastreabilidade (RF x casos de uso x regras)
    ShivaZen-Documento-Tecnico   stack, arquitetura, decisões, banco, segurança, LGPD,
                                 integrações, rotinas, API, testes, implantação e pendências
                                 (seções de docs/PROJECT.md, docs/ARCHITECTURE.md,
                                 docs/specs/padrao-sql.md e aranha_estetica/api/openapi.yaml)

Requisitos, regras e casos de uso: docs/_dados_requisitos.json. Mudou regra/requisito? Atualize o
JSON e o Markdown de origem e rode de novo (no Windows o PDF sai pelo Word):
    python docs/gerar_especificacoes.py [regras|requisitos|tecnico]
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date

import yaml

from _docx_base import BASE, RAIZ, Documento, abrir_word, secao_md

VERSAO = '1.0'
DATA = date(2026, 10, 30).strftime('%d/%m/%Y')
APP = os.path.join(RAIZ, 'aranha_estetica')
D = json.load(open(os.path.join(BASE, '_dados_requisitos.json'), encoding='utf-8'))
RR = D['regras-requisitos']
PROJECT = os.path.join(BASE, 'PROJECT.md')
REGRAS_MD = os.path.join(BASE, 'REGRAS-DE-NEGOCIO.md')
ARCH = os.path.join(BASE, 'ARCHITECTURE.md')
PADRAO_SQL = os.path.join(BASE, 'specs', 'padrao-sql.md')


def _contar_migrations():
    return len([n for n in os.listdir(os.path.join(APP, 'migrations')) if re.match(r'\d{4}_.*\.py$', n)])


def _contar_testes():
    total = 0
    for raiz, _dirs, arquivos in os.walk(os.path.join(APP, 'tests')):
        for nome in arquivos:
            if nome.startswith('test') and nome.endswith('.py'):
                texto = open(os.path.join(raiz, nome), encoding='utf-8').read()
                total += len(re.findall(r'^\s+def test\w*\(', texto, re.M))
    return total


def _decisoes():
    """Linhas | Dn | decisão | motivo | do docs/ARCHITECTURE.md."""
    saida = []
    for linha in open(ARCH, encoding='utf-8').read().splitlines():
        m = re.match(r'^\| (D\d+) \| (.+?) \| (.+?) \|$', linha)
        if m:
            saida.append((m.group(1), m.group(2), m.group(3)))
    return sorted(saida, key=lambda r: int(r[0][1:]))


def _requisitos_por_regra():
    mapa = defaultdict(list)
    for rf in RR['requisitos_funcionais']:
        for rn in rf.get('regras', []):
            mapa[rn].append(rf['id'])
    return mapa


# ═══════════════════════════════ REGRAS DE NEGÓCIO ═══════════════════════════════
def regras(word):
    d = Documento(f'Shiva Zen — Regras de Negócio v{VERSAO}')
    d.capa('Especificação de Regras de Negócio', 'Políticas, limites e comportamentos do sistema', VERSAO, DATA)
    d.sumario()

    d.h1('1. Introdução')
    d.h2('1.1 Objetivo')
    d.par('Este documento especifica as regras de negócio do Shiva Zen — o sistema de gestão da clínica de '
          'estética Jaqueline Aranha: quem pode fazer o quê, como a agenda calcula horários, como um pedido '
          'online vira atendimento, o que acontece em cada mudança de status, como funcionam pacotes, '
          'preços, comissões, cashback, lista de espera, comunicação com a cliente, ficha de saúde, termos, '
          'consentimentos e retenção de dados. Os valores (prazos, limites, janelas) são os que o código '
          'aplica hoje.')
    d.h2('1.2 Como ler')
    d.itens([
        f'O **capítulo 2** é o catálogo: {len(RR["regras"])} regras numeradas (RN01–RN{len(RR["regras"]):02d}), '
        'cada uma com os requisitos funcionais que dependem dela (documento de Requisitos).',
        'O **capítulo 3** detalha as regras por área, com os valores reais, as exceções e onde cada '
        'garantia é aplicada — no serviço da aplicação, no banco (constraint, trigger, procedure) ou nos dois.',
        'O **capítulo 4** traz os diagramas de estados e de atividades que apoiam as regras de status, do '
        'pedido online e da comissão; o **capítulo 5**, o glossário (termo do negócio → nome no código).',
    ])
    d.h2('1.3 Princípios')
    d.itens([
        '**Uma regra, um lugar.** Cada regra vive num serviço (agenda, atendimento, pacotes, comissão, '
        'consentimentos, termos, LGPD); site, painel, portal, Django admin e rotinas chamam o mesmo código.',
        '**O banco garante o que não pode falhar.** Sobreposição de horários, livros-razão imutáveis, prova de '
        'aceite, trilha de alterações e retenção LGPD valem também para SQL manual e para o Django admin.',
        '**Status só pela máquina de estados.** Toda mudança de status do atendimento passa por uma transição '
        'validada, auditada e com os efeitos (pacote, comissão, cashback, faltas, retorno, lista de espera) na '
        'mesma transação: ou tudo grava, ou nada grava.',
        '**A cliente não tem senha.** A identidade é o celular, provado por código SMS de uso único.',
    ])

    d.h1('2. Catálogo das regras de negócio')
    por_regra = _requisitos_por_regra()
    d.tabela(['ID', 'Regra', 'Descrição', 'Requisitos'],
             [[r['id'], r['titulo'], r['descricao'], ', '.join(por_regra.get(r['id'], [])) or '—']
              for r in RR['regras']], widths=[0.5, 1.3, 4.1, 0.8], size=8.5)

    d.h1('3. Especificação detalhada por área')
    texto = open(REGRAS_MD, encoding='utf-8').read()
    corpo = texto[texto.index('\n## 1. '):]
    corpo = re.sub(r'§(\d+)', r'§3.\1', corpo)   # as seções do Markdown viram 3.n neste documento
    d.markdown(corpo, niveis={'##': 2, '###': 3}, renumerar='3')

    d.h1('4. Diagramas de apoio')
    for caminho, legenda in (
            ('04-estados/01-atendimento.png', 'Estados do atendimento (máquina de estados).'),
            ('04-estados/02-compra-de-pacote.png', 'Estados da compra de pacote.'),
            ('04-estados/04-comissao.png', 'Estados da comissão (derivados do livro-razão).'),
            ('05-atividades/01-validar-horario.png', 'Atividade — validação de um horário.'),
            ('05-atividades/02-pedido-online.png', 'Atividade — do pedido online ao atendimento realizado.'),
            ('05-atividades/03-calculo-de-comissao.png', 'Atividade — cálculo da comissão.')):
        d.figura(caminho, legenda)

    d.h1('5. Glossário')
    d.markdown(secao_md(PROJECT, r'Glossário', '###'))
    d.salvar('ShivaZen-Regras-de-Negocio', word)


# ═══════════════════════════════ REQUISITOS ═══════════════════════════════
GRUPOS_ATOR = [
    ('Cliente e visitante do site', lambda a: a.startswith('Cliente')),
    ('Equipe (ADMIN e profissional)', lambda a: a in ('Equipe', 'ADMIN / Profissional')),
    ('Administração (ADMIN)', lambda a: a.startswith('ADMIN') and a != 'ADMIN / Profissional'),
    ('Profissional', lambda a: a == 'Profissional'),
    ('Sistema e integrações', lambda a: a.startswith('Sistema')),
]
PRIORIDADES = [
    ('Essencial', 'Sem ele o sistema não cumpre o objetivo (agendar, atender, cobrar) ou descumpre a LGPD.'),
    ('Importante', 'Necessário para a operação do dia a dia; há contorno manual, com custo.'),
    ('Desejável', 'Melhora a experiência ou a gestão; o sistema funciona sem ele.'),
]


def _grupo(ator):
    for nome, teste in GRUPOS_ATOR:
        if teste(ator):
            return nome
    return 'Outros'


def requisitos(word):
    rfs, rnfs = RR['requisitos_funcionais'], RR['requisitos_nao_funcionais']
    ucs = {u['id']: u for u in D['casos-uso']['casos_de_uso']}
    d = Documento(f'Shiva Zen — Documento de Requisitos v{VERSAO}')
    d.capa('Documento de Requisitos', 'Requisitos funcionais e não funcionais', VERSAO, DATA)
    d.sumario()

    d.h1('1. Introdução')
    d.h2('1.1 Objetivo')
    d.par('Este documento reúne os requisitos do Shiva Zen: o que o sistema faz para cada ator (requisitos '
          'funcionais, RF) e as qualidades que ele precisa ter — segurança, privacidade, confiabilidade, '
          'desempenho, usabilidade, manutenibilidade, implantação e documentação (requisitos não '
          'funcionais, RNF). Cada RF aponta os casos de uso que o realizam e as regras de negócio que o '
          'governam; cada RNF diz como é verificado (teste automatizado ou etapa do CI).')
    d.h2('1.2 Escopo do produto')
    d.par('Plataforma web da clínica: site público com catálogo e agendamento online (a cliente prova o '
          'celular por código SMS, sem senha), área "Meus agendamentos", links individuais (confirmar '
          'presença, ficha de anamnese, termos, NPS), portal da profissional, painel administrativo (agenda, '
          'pedidos, clientes e prontuário, catálogo, pacotes, promoções, comissões, financeiro, NPS, termos, '
          'usuários e auditoria), rotinas automáticas por cron e integrações de SMS, WhatsApp, e-mail, push e '
          'captcha. Fora do escopo: pagamento online, emissão de nota fiscal e aplicativo nativo.')
    d.h2('1.3 Convenções')
    d.tabela(['Item', 'Significado'], [
        ['RFnn', 'Requisito funcional — comportamento observável por um ator.'],
        ['RNFnn', 'Requisito não funcional — qualidade do sistema, com a forma de verificação.'],
        ['UCnn', 'Caso de uso (diagramas e especificação no documento geral do TCC).'],
        ['RNnn', 'Regra de negócio (documento de Regras de Negócio).'],
        *[[nome, texto] for nome, texto in PRIORIDADES],
    ], widths=[1.2, 5.5])
    d.h2('1.4 Origem e manutenção')
    d.par('Os requisitos foram levantados com a dona da clínica a partir do fluxo real de atendimento e '
          'conferidos contra o código: cada RF existe numa tela, rota ou rotina, e cada RNF é cobrado por '
          'teste automatizado ou etapa do CI. A fonte é docs/_dados_requisitos.json, a mesma do documento '
          'geral do TCC; este documento é gerado dela.')

    d.h1('2. Atores')
    d.tabela(['Ator', 'Descrição'], [[a['nome'], a['descricao']] for a in D['casos-uso']['atores']],
             widths=[1.7, 5.0])
    d.par('A cliente nunca é usuária do sistema: identifica-se pelo celular, verificado por código SMS. A '
          'equipe tem dois papéis — ADMIN (dona/recepção, painel completo) e PROFISSIONAL (portal próprio) —, '
          'ambos com segundo fator (TOTP) obrigatório em produção.')

    d.h1('3. Requisitos funcionais')
    por_grupo = defaultdict(list)
    for rf in rfs:
        por_grupo[_grupo(rf['ator'])].append(rf)
    for n, (grupo, _teste) in enumerate(GRUPOS_ATOR, start=1):
        if not por_grupo.get(grupo):
            continue
        d.h2(f'3.{n} {grupo}')
        d.tabela(['ID', 'Requisito', 'Ator', 'Prioridade'],
                 [[rf['id'], rf['descricao'], rf['ator'], rf['prioridade']] for rf in por_grupo[grupo]],
                 widths=[0.55, 4.3, 1.05, 0.8], size=8.5)

    d.h1('4. Requisitos não funcionais')
    categorias = list(dict.fromkeys(r['categoria'] for r in rnfs))
    for n, cat in enumerate(categorias, start=1):
        d.h2(f'4.{n} {cat}')
        d.tabela(['ID', 'Requisito', 'Como é verificado'],
                 [[r['id'], r['descricao'], r['verificacao']] for r in rnfs if r['categoria'] == cat],
                 widths=[0.6, 4.0, 2.1], size=8.5)

    d.h1('5. Casos de uso')
    d.figura('01-casos-de-uso/01-visao-geral.png', 'Casos de uso — visão geral (atores e subsistemas).')
    d.tabela(['ID', 'Caso de uso', 'Atores', 'Inclui'],
             [[u['id'], u['nome'], ', '.join(u['atores']), ', '.join(u.get('inclui', [])) or '—']
              for u in D['casos-uso']['casos_de_uso']], widths=[0.55, 2.2, 3.2, 0.75])

    d.h1('6. Matriz de rastreabilidade')
    d.par('Cada requisito funcional com os casos de uso que o realizam e as regras de negócio que ele aplica. '
          '"—" em caso de uso marca o requisito transversal (exibição, trilha automática), sem interação própria.')
    d.tabela(['RF', 'Prioridade', 'Casos de uso', 'Regras de negócio'],
             [[rf['id'], rf['prioridade'],
               ', '.join(f'{u} {ucs[u]["nome"]}' for u in rf['casos_uso']) or '—',
               ', '.join(rf['regras']) or '—'] for rf in rfs],
             widths=[0.55, 0.85, 3.3, 2.0], size=8.5)
    sem_rf = [u for u in ucs if not any(u in rf['casos_uso'] for rf in rfs)]
    if sem_rf:
        d.par('Casos de uso sem requisito funcional próprio: ' + ', '.join(sem_rf) + '.')

    d.h1('7. Resumo')
    prio = Counter(rf['prioridade'] for rf in rfs)
    d.tabela(['Indicador', 'Quantidade'], [
        ['Requisitos funcionais', len(rfs)],
        *[[f'  {nome}', prio.get(nome, 0)] for nome, _ in PRIORIDADES],
        ['Requisitos não funcionais', len(rnfs)],
        *[[f'  {cat}', sum(1 for r in rnfs if r['categoria'] == cat)] for cat in categorias],
        ['Casos de uso', len(ucs)],
        ['Regras de negócio', len(RR['regras'])],
    ], widths=[4.0, 1.5])
    d.salvar('ShivaZen-Requisitos', word)


# ═══════════════════════════════ DOCUMENTO TÉCNICO ═══════════════════════════════
API_ACESSO = {
    'sessaoEquipe': 'Equipe (ADMIN)', 'sessaoProfissional': 'Profissional', 'sessaoCliente': 'Cliente (sessão SMS)',
    'cronToken': 'X-Cron-Token', 'assinaturaMeta': 'Assinatura HMAC da Meta', 'segredoZenvia': 'Segredo da Zenvia',
    'tokenFeed': 'Token do link',
}


def _secao(d, titulo_md, capitulo, nivel='##'):
    """Renderiza uma seção do PROJECT.md: subtítulos (###) viram <capitulo>.n."""
    sub = '#' * (len(nivel) + 1)
    d.markdown(secao_md(PROJECT, titulo_md, nivel), niveis={sub: 2, sub + '#': 3}, renumerar=capitulo)


def tecnico(word):
    api = yaml.safe_load(open(os.path.join(APP, 'api', 'openapi.yaml'), encoding='utf-8'))
    d = Documento(f'Shiva Zen — Documento Técnico v{VERSAO}')
    d.capa('Documento Técnico', 'Arquitetura, dados, segurança, qualidade e operação', VERSAO, DATA)
    d.sumario()

    d.h1('1. Introdução')
    d.par('Este documento reúne as questões técnicas do Shiva Zen — como o sistema é construído, onde vive '
          'cada responsabilidade, o que o banco garante, como ele é testado, publicado e operado, e o que '
          'ainda está pendente. O conteúdo é extraído dos documentos vivos do repositório (PROJECT.md, '
          'ARCHITECTURE.md, padrão SQL e o contrato OpenAPI), atualizados a cada mudança de código.')
    n_tabelas = len(D['models-core']['models']) + len(D['models-aux']['models'])
    d.tabela(['Item', 'Quantidade'], [
        ['Tabelas do domínio', n_tabelas],
        ['Migrations', _contar_migrations()],
        ['Testes automatizados', _contar_testes()],
        ['Rotas HTTP do sistema', len(D['arquitetura']['rotas'])],
        ['Caminhos documentados em OpenAPI', len(api['paths'])],
        ['Decisões de arquitetura registradas', len(_decisoes())],
    ], widths=[4.0, 1.5])
    d.markdown(secao_md(PROJECT, r'^1\. Visão geral').split('### 1.1')[0])

    d.h1('2. Stack')
    _secao(d, r'^2\. Stack', '2')

    d.h1('3. Arquitetura')
    _secao(d, r'^3\. Arquitetura', '3')
    d.figura('06-componentes/01-camadas.png', 'Componentes — arquitetura em camadas do monólito.')
    d.figura('06-componentes/02-integracoes.png', 'Componentes — integrações externas.')

    d.h1('4. Decisões de arquitetura')
    d.par('Decisões fixadas ao longo do projeto (ADR resumido), com o motivo de cada uma.')
    d.tabela(['ID', 'Decisão', 'Motivo'], _decisoes(), widths=[0.5, 3.0, 3.2], size=8.5)

    d.h1('5. Banco de dados')
    _secao(d, r'^5\. Banco de dados', '5')
    d.h2('5.5 Padrão de nomes e metadados')
    d.markdown(secao_md(PADRAO_SQL, r'^1\.2 Tabelas e colunas', '###'))
    d.markdown(secao_md(PADRAO_SQL, r'^1\.3 Objetos', '###'))
    d.figura('08-dados/mer-conceitual.png', 'MER conceitual — notação de Chen.')
    d.figura('08-dados/00-er-visao-geral.png', 'DER — visão geral das 37 tabelas por área.')

    d.h1('6. Segurança')
    _secao(d, r'^8\. Segurança', '6')

    d.h1('7. LGPD — implementação')
    _secao(d, r'^9\. LGPD', '7')

    d.h1('8. Integrações externas')
    _secao(d, r'^7\. Integrações externas', '8')

    d.h1('9. Rotinas automáticas (jobs)')
    _secao(d, r'^10\. Jobs periódicos', '9')

    d.h1('10. API (OpenAPI 3.1)')
    d.par('O sistema não expõe uma API REST pública: as telas são renderizadas no servidor. As rotas de '
          'máquina — chamadas pelo JavaScript do wizard, do portal da cliente e do painel, pelos provedores '
          '(Meta e Zenvia), pelo agendador dos jobs e pelos calendários externos — estão no contrato '
          f'aranha_estetica/api/openapi.yaml (OpenAPI {api["openapi"]}, {len(api["paths"])} caminhos), navegável no '
          'Swagger UI do painel (/painel/api/, só ADMIN). Um teste reprova rota JSON sem documentação.')
    d.h2('10.1 Contrato das respostas')
    d.tabela(['Caso', 'Corpo JSON', 'Uso'], [
        ['Sucesso', '{"ok": true, ...dados}', 'o front segue com os dados'],
        ['Erro', '{"ok": false, "codigo", "mensagem", ...extra}', 'o front decide pelo código (estável) e mostra a mensagem'],
        ['Fora do envelope', 'formato do provedor / FullCalendar / text/calendar', 'webhooks, saúde, eventos do calendário, feed ICS'],
    ], widths=[1.3, 2.7, 2.7])
    d.h2('10.2 Autenticação')
    d.tabela(['Esquema', 'Onde', 'Descrição'], [
        [nome, f'{e.get("in", "")}: {e.get("name", "")}', e.get('description', '')]
        for nome, e in api['components']['securitySchemes'].items()], widths=[1.4, 1.7, 3.6])
    d.h2('10.3 Operações')
    ordem = [t['name'] for t in api['tags']]
    linhas = []
    for caminho, item in api['paths'].items():
        for metodo in ('get', 'post'):
            if metodo in item:
                op = item[metodo]
                acesso = ' ou '.join(dict.fromkeys(API_ACESSO[n] for req in op.get('security', []) for n in req
                                                   if n in API_ACESSO)) or 'Pública'
                linhas.append([op['tags'][0], f'{metodo.upper()} {caminho}', acesso, op['summary']])
    linhas.sort(key=lambda linha: ordem.index(linha[0]))
    d.tabela(['Grupo', 'Operação', 'Acesso', 'Descrição'], linhas, widths=[1.2, 2.3, 1.2, 2.0], size=8.5)

    d.h1('11. Rotas')
    _secao(d, r'^6\. Rotas', '11')

    d.h1('12. Testes e qualidade')
    _secao(d, r'^12\. Testes e CI', '12')
    d.figura('07-implantacao/02-pipeline-ci.png', 'Atividade — pipeline de integração contínua.')

    d.h1('13. Implantação e configuração')
    _secao(d, r'^13\. Deploy', '13')
    d.figura('07-implantacao/01-railway.png', 'Implantação — Railway.')
    d.h2('13.3 Variáveis de ambiente')
    d.markdown(secao_md(PROJECT, r'^11\. Variáveis de ambiente'), niveis={'###': 3}, renumerar='13.3')

    d.h1('14. Convenções de código')
    _secao(d, r'^15\. Convenções de código', '14')

    d.h1('15. Pendências técnicas e de operação')
    d.par('Itens em aberto (ARCHITECTURE.md §6.2), verificados no código. O que é de operação depende da dona '
          'da clínica (revisão jurídica, contas e chaves dos provedores); o que é de código é de baixa '
          'prioridade e não bloqueia o go-live.')
    abertos, atual = [], None
    for linha in secao_md(ARCH, r'^6\.2 Pendências', '###').splitlines():
        if linha.startswith('- [ ]'):
            atual = [linha[5:].strip()]
            abertos.append(atual)
        elif atual is not None and linha.startswith(' ') and linha.strip():
            atual.append(linha.strip())
        else:
            atual = None
    d.itens([' '.join(item) for item in abertos])
    d.salvar('ShivaZen-Documento-Tecnico', word)


GERADORES = {'regras': regras, 'requisitos': requisitos, 'tecnico': tecnico}

if __name__ == '__main__':
    escolhidos = sys.argv[1:] or list(GERADORES)
    word = abrir_word()
    try:
        for nome in escolhidos:
            GERADORES[nome](word)
    finally:
        if word is not None:
            word.Quit()
