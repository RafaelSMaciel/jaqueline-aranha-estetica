# -*- coding: utf-8 -*-
"""
Gera a Documentação Técnica do Shiva Zen (DOCX + PDF): requisitos, regras de negócio, todos os
diagramas UML (docs/uml/png), arquitetura, modelo de dados, segurança/LGPD, qualidade e implantação.

Fontes: docs/_dados_requisitos.json (requisitos, regras, casos de uso, entidades, rotas — extraídos
do código), docs/uml/png (render do PlantUML: python docs/uml/gerar_uml.py), docs/ARCHITECTURE.md
(decisões) e o próprio código (contagens, docstrings dos serviços).

Uso (Python com python-docx; no Windows o PDF sai pelo Word, com o sumário atualizado):
    python docs/gerar_documentacao.py
"""
import ast
import json
import os
import re
from datetime import date

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.image.image import Image as DocxImage
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

BASE = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(BASE)
PNG = os.path.join(BASE, 'uml', 'png')
DADOS = os.path.join(BASE, '_dados_requisitos.json')
APP = os.path.join(RAIZ, 'aranha_estetica')

VERSAO = '3.0'
DATA = date(2026, 10, 28).strftime('%d/%m/%Y')
# (versao, data, descricao) — acrescente uma linha a cada regeneracao relevante
HISTORICO = [
    ('2.1', '21/06/2026', 'Documento de requisitos + modelagem UML gerado a partir do código '
                          '(pós-auditoria de backend e migração do front-end para base_v2).'),
    ('2.2', '23/09/2026', 'Pós-auditoria pré-produção: OTP em todo agendamento, consentimentos com prova '
                          'de aceite imutável, 2FA obrigatório, histórico do prontuário, agendamento '
                          'interno, Docker e migração atômica (migrations 0040–0046).'),
    ('2.3', '28/10/2026', 'Revisão de outubro/2026 (migrations 0047–0061): agenda unificada, links em hash, '
                          'pacote congelado na venda, comissão como livro-razão, lógica no banco, '
                          'cifragem do dado de saúde, fila de e-mail e saída do Celery.'),
    ('3.0', '28/10/2026', 'Documentação técnica completa: UML refeita em PlantUML (casos de uso, classes '
                          'geradas do código, sequência, estados, atividades, componentes, implantação e '
                          'ER gerado dos models), arquitetura, segurança/LGPD, qualidade e implantação. '
                          'Regras novas: aprovação automática de cliente de confiança, matriz de '
                          'comunicação, pedido pendente vence 1 h antes, retorno marcado com a cliente, '
                          'sessão de pacote prevista, ficha de anamnese por link e ambiente de testes.'),
]
GOLD = RGBColor(0xC4, 0xA3, 0x5A)
DARK = RGBColor(0x4A, 0x34, 0x25)
CINZA = RGBColor(0x66, 0x66, 0x66)

with open(DADOS, encoding='utf-8') as f:
    D = json.load(f)


# ─── números tirados do código (o documento nunca fica com contagem velha) ────────

def _contar_testes():
    total = 0
    for raiz, _dirs, arquivos in os.walk(os.path.join(APP, 'tests')):
        for nome in arquivos:
            if nome.startswith('test') and nome.endswith('.py'):
                arvore = ast.parse(open(os.path.join(raiz, nome), encoding='utf-8').read())
                total += sum(1 for n in ast.walk(arvore)
                             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test'))
    return total


def _contar_migrations():
    return len([n for n in os.listdir(os.path.join(APP, 'migrations')) if re.match(r'\d{4}_.*\.py$', n)])


def _servicos():
    """[(modulo, 1a linha da docstring)] de aranha_estetica/services."""
    pasta = os.path.join(APP, 'services')
    linhas = []
    for nome in sorted(os.listdir(pasta)):
        if not nome.endswith('.py') or nome == '__init__.py':
            continue
        arvore = ast.parse(open(os.path.join(pasta, nome), encoding='utf-8').read())
        doc_mod = (ast.get_docstring(arvore) or '').strip().split('\n')[0]
        linhas.append((f'services.{nome[:-3]}', doc_mod))
    return linhas


def _decisoes():
    """Linhas | Dn | decisão | motivo | do docs/ARCHITECTURE.md."""
    texto = open(os.path.join(BASE, 'ARCHITECTURE.md'), encoding='utf-8').read()
    saida = []
    for linha in texto.splitlines():
        m = re.match(r'^\| (D\d+) \| (.+?) \| (.+?) \|$', linha)
        if m:
            limpar = lambda s: re.sub(r'[*`]', '', s)  # noqa: E731
            saida.append((m.group(1), limpar(m.group(2)), limpar(m.group(3))))
    return sorted(saida, key=lambda r: int(r[0][1:]))


N_TESTES = _contar_testes()
N_MIGRATIONS = _contar_migrations()
N_TABELAS = len(D['models-core']['models']) + len(D['models-aux']['models'])

doc = Document()
normal = doc.styles['Normal']
normal.font.name = 'Calibri'
normal.font.size = Pt(10.5)
for lvl, sz in ((1, 16), (2, 13), (3, 11.5)):
    st = doc.styles[f'Heading {lvl}']
    st.font.color.rgb = DARK
    st.font.size = Pt(sz)
    st.font.name = 'Calibri'

FIGURA = [0]


def _shade(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:fill'), hexcolor)
    tcPr.append(shd)


def add_table(headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = 'Light Grid Accent 1'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ''
        run = hdr[i].paragraphs[0].add_run(h)
        run.bold = True
        run.font.size = Pt(9.5)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        _shade(hdr[i], '7A5F1F')
    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ''
            r = cells[i].paragraphs[0].add_run(str(val))
            r.font.size = Pt(9)
    if widths:
        for col, w in enumerate(widths):
            for cell in t.columns[col].cells:
                cell.width = Inches(w)
    doc.add_paragraph()
    return t


def _orientar(paisagem):
    """Garante a orientacao da pagina atual. Secao nova (= pagina nova) so quando ela muda."""
    sec = doc.sections[-1]
    if (sec.orientation == WD_ORIENT.LANDSCAPE) == paisagem:
        return False
    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    sec.orientation = WD_ORIENT.LANDSCAPE if paisagem else WD_ORIENT.PORTRAIT
    sec.page_width, sec.page_height = sec.page_height, sec.page_width
    return True


def figura(caminho, legenda, descricao=None):
    """Diagrama (docs/uml/png/<caminho>) numerado; largo vai em paisagem, alto em retrato."""
    path = os.path.join(PNG, caminho)
    if not os.path.exists(path):
        doc.add_paragraph(f'[diagrama ausente: {caminho} — rode python docs/uml/gerar_uml.py]')
        return
    img = DocxImage.from_file(path)
    proporcao = img.px_height / img.px_width
    paisagem = proporcao <= 0.75
    if paisagem:
        if not _orientar(True):
            doc.add_page_break()
    else:
        _orientar(False)
    if descricao:
        par(descricao, size=9.5)
    sec = doc.sections[-1]
    max_w = sec.page_width - sec.left_margin - sec.right_margin
    max_h = sec.page_height - sec.top_margin - sec.bottom_margin - Inches(1.1 if descricao else 0.8)
    largura = min(max_w, int(max_h / proporcao))
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(path, width=largura)
    FIGURA[0] += 1
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = cap.add_run(f'Figura {FIGURA[0]} — {legenda}')
    r.italic = True
    r.font.size = Pt(8.5)
    r.font.color.rgb = CINZA


def campo(run, instrucao, texto=''):
    """Campo do Word (sumario, numero de pagina) no run: begin + instrucao [+ texto] + end."""
    def marca(tipo):
        el = OxmlElement('w:fldChar')
        el.set(qn('w:fldCharType'), tipo)
        return el
    instr = OxmlElement('w:instrText')
    instr.set(qn('xml:space'), 'preserve')
    instr.text = instrucao
    elementos = [marca('begin'), instr]
    if texto:
        t = OxmlElement('w:t')
        t.text = texto
        elementos += [marca('separate'), t]
    elementos.append(marca('end'))
    for el in elementos:
        run._r.append(el)


def h1(txt, nova_pagina=True):
    _orientar(False)
    if nova_pagina:
        doc.add_page_break()
    doc.add_heading(txt, level=1)


def h2(txt):
    _orientar(False)
    doc.add_heading(txt, level=2)


def par(txt, size=10.5):
    p = doc.add_paragraph()
    r = p.add_run(txt)
    r.font.size = Pt(size)
    return p


def itens(lista, numerada=False, size=9.5):
    for item in lista:
        p = doc.add_paragraph(style='List Number' if numerada else 'List Bullet')
        p.add_run(item).font.size = Pt(size)


# ===================== CAPA =====================
for _ in range(4):
    doc.add_paragraph()
for texto, tamanho, cor, negrito in (
        ('SHIVA ZEN', 34, GOLD, True),
        ('Sistema de Gestão para Clínica de Estética', 15, DARK, False),
        ('Jaqueline Aranha Estética', 12, CINZA, False)):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(texto)
    r.bold = negrito
    r.font.size = Pt(tamanho)
    r.font.color.rgb = cor
for _ in range(2):
    doc.add_paragraph()
tit = doc.add_paragraph()
tit.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = tit.add_run('Documentação Técnica do Projeto\nRequisitos, Modelagem UML, Arquitetura e Implantação')
r.bold = True
r.font.size = Pt(16)
for _ in range(6):
    doc.add_paragraph()
meta = doc.add_paragraph()
meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
meta.add_run(f'Versão {VERSAO}  |  {DATA}').font.size = Pt(11)

# ===================== SUMARIO =====================
doc.add_page_break()
titulo_sumario = doc.add_paragraph()
r = titulo_sumario.add_run('Sumário')   # sem estilo de titulo: nao entra no proprio sumario
r.bold = True
r.font.size = Pt(16)
r.font.color.rgb = DARK
toc_p = doc.add_paragraph()
run = toc_p.add_run()
campo(run, 'TOC \\o "1-2" \\h \\z \\u', 'Atualize este campo no Word (Ctrl+A, F9) para gerar o sumário.')

# ===================== 1. INTRODUCAO =====================
h1('1. Introdução')
h2('1.1 Objetivo do documento')
par('Este documento descreve de forma completa o sistema Shiva Zen — plataforma web de gestão da clínica '
    'de estética Jaqueline Aranha: requisitos funcionais e não funcionais, regras de negócio, a '
    'modelagem UML (casos de uso, classes, sequência, estados, atividades, componentes e implantação), '
    'o modelo de dados, a arquitetura, a segurança e a conformidade com a LGPD, a estratégia de '
    'qualidade e o roteiro de implantação. O conteúdo é derivado do código-fonte: os diagramas de '
    'classes e de dados são gerados a partir dos models do Django e as contagens são lidas do '
    'repositório na geração do documento.')
h2('1.2 Contexto e problema')
par('A clínica atendia por WhatsApp e agenda de papel: horários perdidos, faltas sem controle, '
    'pacotes de sessões anotados à mão, comissões calculadas em planilha e nenhum registro formal de '
    'consentimento para dados de saúde. O sistema substitui esse fluxo por um agendamento online '
    'com confirmação de celular, uma agenda única para a equipe, o controle de pacotes e de comissões '
    'em livro-razão, o prontuário com histórico e a prova de cada aceite exigido pela LGPD.')
h2('1.3 Escopo')
par('Site público com catálogo, promoções e agendamento online (a cliente não tem senha: prova o '
    'celular por código SMS); área "Meus agendamentos" (acompanhar, cancelar e remarcar); links '
    'individuais para confirmar presença, avaliar o atendimento (NPS), responder a ficha de anamnese e '
    'aceitar termos; portal da profissional; painel administrativo (agenda, aprovação de pedidos, '
    'agendamento pela recepção, clientes e prontuário, catálogo, pacotes, promoções, comissões, '
    'financeiro, NPS, termos, formulários, usuários, configurações e auditoria); rotinas automáticas '
    'disparadas por cron (fila de e-mail, lembretes, NPS, pacotes, expiração de pedidos, retenção LGPD) '
    'e integrações (SMS, WhatsApp, e-mail, push, captcha e monitoramento de erros).')
h2('1.4 Números do projeto')
add_table(['Item', 'Quantidade'], [
    ['Entidades (tabelas do domínio)', N_TABELAS],
    ['Migrations do banco', N_MIGRATIONS],
    ['Testes automatizados', N_TESTES],
    ['Requisitos funcionais', len(D['regras-requisitos']['requisitos_funcionais'])],
    ['Requisitos não funcionais', len(D['regras-requisitos']['requisitos_nao_funcionais'])],
    ['Regras de negócio catalogadas', len(D['regras-requisitos']['regras'])],
    ['Casos de uso especificados', len(D['casos-uso']['casos_de_uso'])],
    ['Rotas HTTP', len(D['arquitetura']['rotas'])],
], widths=[4.0, 1.5])

# ===================== 2. VISAO GERAL =====================
h1('2. Visão Geral da Solução')
h2('2.1 Tecnologias')
add_table(['Camada', 'Tecnologia', 'Uso'], [
    ['Back-end', 'Python 3.12, Django 5.2', 'Monólito renderizado no servidor; regras nos serviços'],
    ['Banco de dados', 'PostgreSQL 18', 'Constraints, EXCLUDE, triggers, procedures e views'],
    ['Front-end', 'Vite, Tailwind CSS 4, Alpine.js (CSP), django-cotton', 'Bundle versionado, tokens de design, PWA'],
    ['Servidor', 'gunicorn + WhiteNoise em Docker', 'Contêiner não-root, estáticos com hash'],
    ['Hospedagem', 'Railway', 'Ambiente de produção (main) e de testes (dev)'],
    ['Autenticação', 'Django auth + django-otp (TOTP) + django-axes', 'Equipe com 2FA obrigatório'],
    ['Mensageria', 'Resend (anymail), Zenvia, WhatsApp Cloud API, Web Push', 'E-mail, SMS do OTP, lembretes, push'],
    ['Qualidade', 'Django test runner, ruff, mypy, import-linter, Playwright, pip-audit', 'CI com 8 jobs'],
], widths=[1.3, 2.4, 3.0])
h2('2.2 Perfis de acesso')
par('Três perfis usam o sistema: a cliente (sem senha, identificada pelo celular verificado por SMS), '
    'a profissional (portal próprio, com 2FA) e a administradora (dona/recepção, painel completo, com '
    '2FA). O agendador externo (cron) é um ator de sistema autenticado por token.')

# ===================== 3. ATORES =====================
h1('3. Atores do Sistema')
add_table(['Ator', 'Descrição'], [[a['nome'], a['descricao']] for a in D['casos-uso']['atores']], widths=[1.7, 5.0])

# ===================== 4-6. REQUISITOS E REGRAS =====================
h1('4. Requisitos Funcionais')
add_table(['ID', 'Ator', 'Descrição'],
          [[rf['id'], rf['ator'], rf['descricao']] for rf in D['regras-requisitos']['requisitos_funcionais']],
          widths=[0.6, 1.4, 4.7])
h1('5. Requisitos Não Funcionais')
add_table(['ID', 'Categoria', 'Descrição'],
          [[r['id'], r['categoria'], r['descricao']] for r in D['regras-requisitos']['requisitos_nao_funcionais']],
          widths=[0.6, 1.2, 4.9])
h1('6. Regras de Negócio')
add_table(['ID', 'Título', 'Descrição'],
          [[r['id'], r['titulo'], r['descricao']] for r in D['regras-requisitos']['regras']],
          widths=[0.6, 1.6, 4.5])

# ===================== 7. CASOS DE USO =====================
h1('7. Casos de Uso')
par('Os casos de uso estão divididos por subsistema: uma visão geral com todos os atores e um '
    'diagrama por área. «include» marca o passo sempre executado; «extend», o opcional ou condicional.')
CASOS = [
    ('01-casos-de-uso/01-visao-geral.png', 'Casos de uso — visão geral (atores e subsistemas).'),
    ('01-casos-de-uso/02-site-publico.png', 'Casos de uso — site público e agendamento online.'),
    ('01-casos-de-uso/03-area-da-cliente.png', 'Casos de uso — área da cliente e links recebidos.'),
    ('01-casos-de-uso/04-painel-agenda.png', 'Casos de uso — painel: operação da agenda.'),
    ('01-casos-de-uso/05-painel-gestao.png', 'Casos de uso — painel: gestão da clínica.'),
    ('01-casos-de-uso/06-portal-profissional.png', 'Casos de uso — portal da profissional.'),
    ('01-casos-de-uso/07-rotinas-automaticas.png', 'Casos de uso — rotinas automáticas e webhooks.'),
]
for caminho, legenda in CASOS:
    figura(caminho, legenda)
h2('7.1 Especificação dos casos de uso')
add_table(['ID', 'Caso de uso', 'Atores', 'Fluxo principal'],
          [[uc['id'], uc['nome'], ', '.join(uc['atores']), uc['fluxo']] for uc in D['casos-uso']['casos_de_uso']],
          widths=[0.5, 1.4, 1.5, 3.3])

# ===================== 8. MODELO DE DOMINIO =====================
h1('8. Modelo de Domínio (Diagramas de Classes)')
par('Os diagramas de classes são gerados a partir dos models (docs/uml/gerar_uml.py): atributos com o '
    'tipo Python, enumerações (TextChoices), métodos públicos — inclusive a máquina de estados do '
    'atendimento — e os QuerySets de domínio. Cada diagrama mostra as associações que saem das '
    'classes da parte; as classes de outras partes aparecem em cinza, só com o nome.')
for i, (caminho, legenda) in enumerate([
        ('01-classes-agenda-atendimento.png', 'Classes — agenda e atendimento.'),
        ('02-classes-clientes-saude.png', 'Classes — clientes, saúde e formulários.'),
        ('03-classes-financeiro.png', 'Classes — pacotes, comissão e carteira.'),
        ('04-classes-comunicacao.png', 'Classes — comunicação, links de acesso e verificação.'),
        ('05-classes-lgpd-seguranca.png', 'Classes — LGPD, termos, auditoria e acesso.')]):
    figura('02-classes/' + caminho, legenda)
h2('8.1 Dicionário de entidades')
linhas = [[m['nome'], m.get('tabela', ''), m['descricao']]
          for grupo in ('models-core', 'models-aux') for m in D[grupo]['models']]
add_table(['Entidade', 'Tabela', 'Descrição'], linhas, widths=[1.5, 1.4, 3.8])

# ===================== 9. CAMADA DE SERVICOS =====================
h1('9. Camada de Serviços (Casos de Uso da Aplicação)')
par('As views só traduzem HTTP: validam a entrada e chamam os serviços, onde vivem as regras de '
    'negócio e os efeitos colaterais. Os diagramas abaixo são gerados por análise do código (AST): '
    'cada módulo com suas operações públicas e as dependências entre módulos.')
for caminho, legenda in (
        ('10-servicos-agenda.png', 'Serviços — agenda e atendimento.'),
        ('11-servicos-clinico-comunicacao.png', 'Serviços — fichas, termos, LGPD e comunicação.'),
        ('12-servicos-financeiro.png', 'Serviços — financeiro e relatórios.')):
    figura('02-classes/' + caminho, legenda)
h2('9.1 Responsabilidade de cada serviço')
add_table(['Módulo', 'Responsabilidade (docstring do módulo)'], _servicos(), widths=[2.0, 4.7])

# ===================== 10. ESTADOS =====================
h1('10. Diagramas de Estados')
par('O status do atendimento é governado por uma máquina de estados finitos (Atendimento.TRANSICOES): '
    'cada transição trava a linha (SELECT FOR UPDATE), valida a origem, grava o status e a auditoria e '
    'aplica os efeitos na mesma transação — se um efeito falha, nada é gravado. Transição inválida '
    'levanta Atendimento.TransicaoInvalida com mensagem legível.')
for caminho, legenda in (
        ('04-estados/01-atendimento.png', 'Estados — Atendimento.'),
        ('04-estados/02-compra-de-pacote.png', 'Estados — compra de pacote.'),
        ('04-estados/03-notificacao.png', 'Estados — notificação (fila de e-mail e mensagens).'),
        ('04-estados/04-comissao.png', 'Estados — comissão (derivado do livro-razão).')):
    figura(caminho, legenda)
h2('10.1 Tabela de transições do atendimento')
add_table(['Estado origem', 'Estado destino', 'Ação / gatilho'],
          [[t['de'], t['para'], t['acao']] for t in D['fsm']['transicoes'] if t['para'] != '(nenhum)'],
          widths=[1.6, 1.6, 3.5])
h2('10.2 Efeitos automáticos')
itens(D['fsm']['efeitos'])

# ===================== 11. SEQUENCIA =====================
h1('11. Diagramas de Sequência')
SEQ = [
    ('01-agendamento-online', 'agendamento online (wizard + OTP).',
     'Do catálogo ao atendimento criado: horários livres calculados pelo SlotService, verificação do '
     'celular por SMS, trava do horário e gravação transacional com os aceites e a ficha.'),
    ('02-aprovacao-pedido', 'aprovação de pedido pendente.',
     'Botão, lote ou seletor de status passam pelo mesmo serviço — a cliente sempre recebe a resposta.'),
    ('03-realizado-efeitos', 'marcar realizado e os efeitos.',
     'Faltas, pacote, comissão e cashback na mesma transação, garantidos também por triggers.'),
    ('04-cancelamento-pela-cliente', 'cancelamento pela cliente.',
     'Estornos, aviso à lista de espera e push à profissional.'),
    ('05-lembrete-e-confirmacao', 'lembrete D-1 e confirmação de presença.', None),
    ('06-nps-e-pesquisa', 'NPS e pesquisa pós-atendimento.', None),
    ('07-login-equipe-2fa', 'login da equipe com 2FA.', None),
    ('08-fila-de-email', 'fila de e-mail transacional.', None),
    ('09-lgpd-retencao', 'LGPD: pseudonimização e retenção.', None),
]
for nome, legenda, descricao in SEQ:
    figura(f'03-sequencia/{nome}.png', 'Sequência — ' + legenda, descricao)
h2('11.1 Passo a passo do agendamento online')
itens(D['arquitetura']['fluxo_agendamento_otp'], numerada=True)

# ===================== 12. ATIVIDADES =====================
h1('12. Diagramas de Atividades')
for caminho, legenda in (
        ('05-atividades/01-validar-horario.png', 'Atividade — validação de um horário.'),
        ('05-atividades/02-pedido-online.png', 'Atividade — do pedido online ao atendimento realizado.'),
        ('05-atividades/03-calculo-de-comissao.png', 'Atividade — cálculo da comissão.')):
    figura(caminho, legenda)

# ===================== 13. ARQUITETURA =====================
h1('13. Arquitetura')
par('Monólito Django em camadas: borda HTTP (urls e views separadas por área: público, conta, painel, '
    'profissional e infraestrutura), formulários, serviços (casos de uso), domínio (models, máquina de '
    'estados e QuerySets) e banco. O PostgreSQL é a fonte única da escrita: sobreposição de horários, '
    'imutabilidade dos livros-razão e trilha de alterações são garantidas por constraints e triggers, '
    'valendo também para o Django admin e para SQL manual. Não há fila assíncrona (Celery): as rotinas '
    'são funções chamadas por um cron externo via HTTP autenticado, e o e-mail é uma fila na tabela '
    'de notificações, enviada após o commit.')
figura('06-componentes/01-camadas.png', 'Componentes — arquitetura em camadas.')
figura('06-componentes/02-integracoes.png', 'Componentes — integrações externas.')
h2('13.1 Componentes')
add_table(['Componente', 'Papel'], [[c['nome'], c['papel']] for c in D['arquitetura']['componentes']],
          widths=[1.8, 4.9])
h2('13.2 Integrações externas')
add_table(['Serviço', 'Uso'], [[i['nome'], i['uso']] for i in D['arquitetura']['integracoes_externas']],
          widths=[1.8, 4.9])
h2('13.3 Decisões de arquitetura (ADR)')
add_table(['ID', 'Decisão', 'Motivo'], _decisoes(), widths=[0.5, 3.2, 3.0])

# ===================== 14. MODELO DE DADOS =====================
h1('14. Modelo de Dados')
par('O modelo físico é gerado dos models com os tipos reais do PostgreSQL, chaves (PK, FK, UK) e '
    'nulidade (* = obrigatório). Nomes seguem o padrão SQL do projeto (tabelas no singular, colunas '
    'eh_*, constraints ck_/uq_/ex_/fk_ e índices ix_). A visão geral mostra todas as tabelas; as demais, '
    'uma área do domínio cada.')
for caminho, legenda in (
        ('00-er-visao-geral.png', 'Modelo de dados — visão geral.'),
        ('01-er-agenda-atendimento.png', 'Modelo de dados — agenda e atendimento.'),
        ('02-er-clientes-saude.png', 'Modelo de dados — clientes, saúde e formulários.'),
        ('03-er-financeiro.png', 'Modelo de dados — pacotes, comissão e carteira.'),
        ('04-er-comunicacao.png', 'Modelo de dados — comunicação, links e verificação.'),
        ('05-er-lgpd-seguranca.png', 'Modelo de dados — LGPD, termos, auditoria e acesso.')):
    figura('08-dados/' + caminho, legenda)
h2('14.1 Lógica no banco')
itens([
    'EXCLUDE (btree_gist) impede dois atendimentos ativos sobrepostos da mesma profissional.',
    'Livros-razão append-only (movimento_comissao, movimento_carteira): trigger recusa UPDATE/DELETE; '
    'no máximo um crédito de comissão ativo por atendimento.',
    'Trilha historico_alteracao preenchida por trigger em toda escrita das tabelas sensíveis, com autor, '
    'IP e origem da requisição (variáveis de sessão definidas pelo middleware).',
    'Procedures sp_anonimizar_cliente e sp_aplicar_retencao_lgpd: a regra de retenção e pseudonimização '
    'vive no banco e é a mesma para o job, o admin e SQL manual.',
    'Funções e views de leitura (saldo de pacote, horários livres, faturamento) com paridade testada '
    'contra a implementação em Python.',
    'Dado de saúde (prontuário, respostas de ficha) gravado cifrado (Fernet, chaves rotacionáveis).',
])

# ===================== 15. SEGURANCA E LGPD =====================
h1('15. Segurança e Conformidade com a LGPD')
h2('15.1 Segurança da aplicação')
itens([
    'Equipe: senha (PBKDF2), segundo fator TOTP obrigatório, bloqueio por tentativas (django-axes) e '
    'ratelimit; a cliente não tem senha — prova a posse do celular por código SMS de uso único.',
    'Links enviados à cliente (confirmação, NPS, ficha, termo, agenda ICS) guardam só o hash SHA-256 do '
    'token, com validade e uso único quando aplicável; logs nunca registram tokens.',
    'Cabeçalhos: HSTS, CSP com nonce (Alpine na build CSP, sem eval), X-Frame-Options, Referrer-Policy; '
    'cookies Secure/HttpOnly/SameSite; CSRF em todo POST.',
    'Webhooks e cron autenticados (token no cabeçalho, comparação em tempo constante, allowlist de IP).',
    'Auditoria (LogAuditoria) de toda ação administrativa e de cada leitura ou escrita de dado de saúde.',
    'Ambiente de testes publicado isolado: SMS só no log, e-mail e WhatsApp apenas para destinatários '
    'autorizados, robots e cabeçalho noindex.',
])
h2('15.2 LGPD')
itens([
    'Base legal por finalidade: execução de contrato (agendamento e mensagens transacionais), '
    'consentimento específico e destacado para dado de saúde (art. 11) e consentimento para '
    'marketing, lembrete e pesquisa por WhatsApp — com histórico (quem, quando, texto exibido, IP).',
    'Prova de aceite imutável: versão do termo, hash do texto, IP e user-agent.',
    'Direitos da titular: cópia dos dados (com OTP), descadastro por link e pseudonimização a pedido.',
    'Retenção: clientes inativas há 5 anos são pseudonimizadas; registros de saúde e contratos ficam '
    'pelo prazo legal; fichas de pedidos não realizados são apagadas em 90 dias.',
    'Minimização: feed de agenda da profissional sem dado de saúde; inventário de dados por coluna '
    '(o que é apagado, pseudonimizado ou retido).',
])

# ===================== 16. QUALIDADE =====================
h1('16. Qualidade e Testes')
par(f'A suíte tem {N_TESTES} testes automatizados (Django test runner), executados no SQLite e no '
    'PostgreSQL 18 — onde também são testadas as constraints, triggers, procedures e a paridade das '
    'funções SQL com o Python. Há testes de data migrations, de rotas (toda URL mapeada é exercida com '
    'cada perfil de acesso) e um teste de navegador (Playwright) do wizard de agendamento.')
figura('07-implantacao/02-pipeline-ci.png', 'Atividade — pipeline de integração contínua.')
add_table(['Verificação', 'Ferramenta', 'Garante'], [
    ['Estilo e erros', 'ruff', 'código sem erro estático; datas sempre com fuso'],
    ['Camadas', 'import-linter', 'utils, db, jobs e comandos nunca importam views'],
    ['Tipos', 'mypy (services)', 'contratos dos casos de uso'],
    ['Migrations', 'makemigrations --check', 'models e banco em dia'],
    ['Cobertura', 'coverage (mínimo no CI)', 'regressão de cobertura bloqueia o merge'],
    ['Dependências', 'pip-audit', 'nenhuma vulnerabilidade conhecida'],
    ['Navegador', 'Playwright + axe-core', 'fluxo real do wizard e acessibilidade'],
], widths=[1.4, 1.8, 3.5])

# ===================== 17. IMPLANTACAO =====================
h1('17. Implantação e Operação')
par('A imagem Docker é construída em três estágios (front com Node 22, dependências e runtime Python '
    '3.12 slim, usuário não-root). No Railway, cada environment tem o serviço web e o seu PostgreSQL 18; '
    'o pre-deploy aplica todas as migrations numa única transação (migrate_atomico) e o tráfego só '
    'muda quando /healthz/ responde. A branch dev publica o ambiente de testes (perfil staging) e a main '
    'publica a produção, protegida pelos jobs obrigatórios do CI.')
figura('07-implantacao/01-railway.png', 'Implantação — Railway.')
add_table(['Ambiente', 'Branch / perfil', 'Características'], [
    ['Desenvolvimento local', 'DJANGO_ENV=dev', 'DEBUG, SQLite ou Postgres local, SMS em log'],
    ['Testes publicado', 'dev / DJANGO_ENV=staging', 'igual à produção + travas de envio, faixa e noindex, seed --demo'],
    ['Produção', 'main / DJANGO_ENV=prod', 'HTTPS, Postgres obrigatório, cifragem, 2FA, Sentry'],
], widths=[1.6, 1.9, 3.2])
h2('17.1 Rotinas (cron)')
add_table(['Job', 'Quando', 'O que faz'], [
    ['emails', 'a cada 15 min', 'reenvia a fila de e-mail'],
    ['lembrete_diario', '08:00', 'lembrete D-1 por WhatsApp (com consentimento)'],
    ['nps_24h / detrator_alerta', '10:00 / 10:30', 'pesquisa NPS e alerta de nota baixa'],
    ['limpeza_status', 'de hora em hora', 'vence pedido pendente a 1 h do início; lista atendimentos sem desfecho'],
    ['pacote_expirando / pacote_expirar', '07:00 / 00:30', 'aviso de validade e expiração de pacotes'],
    ['aniversario', '09:00', 'felicitação (com consentimento de marketing)'],
    ['lgpd_purgar', 'domingo 03:00', 'retenção e pseudonimização'],
    ['housekeeping / feriados', '04:00 / dia 1', 'limpeza técnica e carga de feriados'],
], widths=[2.0, 1.3, 3.4])

# ===================== 18. ROTAS =====================
h1('18. Rotas')
add_table(['Área', 'Rota', 'Função'], [[r['grupo'], r['rota'], r['funcao']] for r in D['arquitetura']['rotas']],
          widths=[1.0, 2.9, 2.8])

# ===================== GLOSSARIO E HISTORICO =====================
h1('Glossário')
add_table(['Termo', 'Significado'], [
    ['Atendimento', 'Uma sessão marcada (procedimento, profissional, cliente, horário).'],
    ['Pedido pendente', 'Agendamento online aguardando a aprovação da profissional.'],
    ['Cliente de confiança', 'Já atendida, sem falta em aberto e com menos de 2 horários futuros: o pedido já nasce aprovado.'],
    ['Retorno', 'Sessão de acompanhamento sem custo, marcada dentro da janela do procedimento.'],
    ['Pacote', 'Conjunto de sessões vendido com validade; os itens são congelados na venda.'],
    ['Livro-razão', 'Tabela só de inclusão (crédito, estorno, pagamento): o saldo é a soma dos lançamentos.'],
    ['OTP', 'Código de uso único enviado por SMS para provar a posse do celular.'],
    ['NPS', 'Net Promoter Score: nota de 0 a 10 de recomendação.'],
    ['FSM', 'Máquina de estados finitos que governa o status do atendimento.'],
    ['Pseudonimização', 'Substituição dos dados pessoais por valores sem vínculo com a titular, mantendo o histórico.'],
], widths=[1.6, 5.1])
h1('Histórico de Versões')
add_table(['Versão', 'Data', 'Descrição'], [list(linha) for linha in HISTORICO], widths=[0.9, 1.2, 4.6])

# ---- rodape com numero de pagina (secoes em paisagem herdam o rodape da 1a) ----
for i, section in enumerate(doc.sections):
    footer = section.footer
    if i > 0 and footer.is_linked_to_previous:
        continue
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run('Shiva Zen — Documentação Técnica v' + VERSAO + '   |   Pág. ')
    run.font.size = Pt(8)
    campo(run, 'PAGE')

out_docx = os.path.join(BASE, 'ShivaZen-Documentacao.docx')
out_pdf = os.path.join(BASE, 'ShivaZen-Documentacao.pdf')
rascunho = os.path.join(BASE, '~rascunho-documentacao.docx')
doc.save(rascunho)
print(f'{FIGURA[0]} figuras, {N_TESTES} testes, {N_MIGRATIONS} migrations')

# ---- sumario atualizado + PDF pelo Word (Windows). Sem Word: fica o DOCX sem o sumario
# preenchido (abra no Word, F9 no sumario e Salvar como PDF).
try:
    import win32com.client

    word = win32com.client.DispatchEx('Word.Application')
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        d = word.Documents.Open(rascunho, ReadOnly=False, AddToRecentFiles=False)
        for toc in d.TablesOfContents:
            toc.Update()
        d.SaveAs2(out_docx, FileFormat=16)            # wdFormatXMLDocument
        d.ExportAsFixedFormat(OutputFileName=out_pdf, ExportFormat=17,   # wdExportFormatPDF
                              OptimizeFor=0, CreateBookmarks=1)          # marcadores = sumario do PDF
        d.Close(False)
    finally:
        word.Quit()
    os.remove(rascunho)
    print('DOCX:', out_docx)
    print('PDF :', out_pdf)
except Exception as e:  # noqa: BLE001
    os.replace(rascunho, out_docx)
    print('DOCX:', out_docx, '(sumario e PDF pendentes: Word indisponivel)', repr(e))
