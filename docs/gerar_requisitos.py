# -*- coding: utf-8 -*-
"""
Gera o documento de Requisitos do Sistema Shiva Zen (DOCX + PDF) a partir dos
dados extraidos do codigo real (docs/_dados_requisitos.json) e dos diagramas UML
renderizados em docs/diagramas/*.png.

Uso: python docs/gerar_requisitos.py
"""
import json
import os
from datetime import date

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

BASE = os.path.dirname(os.path.abspath(__file__))
DIAG = os.path.join(BASE, 'diagramas')
DADOS = os.path.join(BASE, '_dados_requisitos.json')

VERSAO = '2.1'
DATA = date(2026, 6, 21).strftime('%d/%m/%Y')
GOLD = RGBColor(0xC4, 0xA3, 0x5A)
DARK = RGBColor(0x4A, 0x34, 0x25)

with open(DADOS, encoding='utf-8') as f:
    D = json.load(f)

doc = Document()

# ---- estilos base ----
normal = doc.styles['Normal']
normal.font.name = 'Calibri'
normal.font.size = Pt(10.5)

for lvl, sz in ((1, 16), (2, 13)):
    st = doc.styles[f'Heading {lvl}']
    st.font.color.rgb = DARK
    st.font.size = Pt(sz)
    st.font.name = 'Calibri'


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


def add_image(nome, legenda, largura=6.4, paisagem=False):
    path = os.path.join(DIAG, nome)
    if not os.path.exists(path):
        doc.add_paragraph(f'[diagrama ausente: {nome}]')
        return
    if paisagem:
        sec = doc.add_section(WD_SECTION.NEW_PAGE)
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = sec.page_height, sec.page_width
        largura = 9.2
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(path, width=Inches(largura))
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = cap.add_run(legenda)
    r.italic = True
    r.font.size = Pt(8.5)
    r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    if paisagem:
        sec2 = doc.add_section(WD_SECTION.NEW_PAGE)
        sec2.orientation = WD_ORIENT.PORTRAIT
        sec2.page_width, sec2.page_height = sec2.page_height, sec2.page_width


def h1(txt):
    doc.add_heading(txt, level=1)


def h2(txt):
    doc.add_heading(txt, level=2)


def par(txt, size=10.5):
    p = doc.add_paragraph()
    r = p.add_run(txt)
    r.font.size = Pt(size)
    return p


# ===================== CAPA =====================
for _ in range(4):
    doc.add_paragraph()
cap = doc.add_paragraph()
cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = cap.add_run('SHIVA ZEN')
r.bold = True
r.font.size = Pt(34)
r.font.color.rgb = GOLD
sub = doc.add_paragraph()
sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = sub.add_run('Sistema de Gestao para Clinica de Estetica')
r.font.size = Pt(15)
r.font.color.rgb = DARK
sub2 = doc.add_paragraph()
sub2.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = sub2.add_run('Jaqueline Aranha Estetica')
r.font.size = Pt(12)
r.italic = True
for _ in range(2):
    doc.add_paragraph()
tit = doc.add_paragraph()
tit.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = tit.add_run('Documento de Especificacao de Requisitos\ne Modelagem UML')
r.bold = True
r.font.size = Pt(16)
for _ in range(6):
    doc.add_paragraph()
meta = doc.add_paragraph()
meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
meta.add_run(f'Versao {VERSAO}  |  {DATA}').font.size = Pt(11)

# ===================== SUMARIO =====================
doc.add_page_break()
h1('Sumario')
toc_p = doc.add_paragraph()
run = toc_p.add_run()
fldChar = OxmlElement('w:fldChar'); fldChar.set(qn('w:fldCharType'), 'begin')
instr = OxmlElement('w:instrText'); instr.set(qn('xml:space'), 'preserve')
instr.text = 'TOC \\o "1-2" \\h \\z \\u'
fldChar2 = OxmlElement('w:fldChar'); fldChar2.set(qn('w:fldCharType'), 'separate')
t_run = OxmlElement('w:t'); t_run.text = 'Atualize este campo no Word (Ctrl+A, F9) para gerar o sumario.'
fldChar3 = OxmlElement('w:fldChar'); fldChar3.set(qn('w:fldCharType'), 'end')
for el in (fldChar, instr, fldChar2, t_run, fldChar3):
    run._r.append(el)

# ===================== 1. INTRODUCAO =====================
doc.add_page_break()
h1('1. Introducao')
h2('1.1 Objetivo')
par('Este documento especifica os requisitos funcionais e nao-funcionais, as regras '
    'de negocio e a modelagem UML do sistema Shiva Zen — plataforma web de gestao para '
    'a clinica de estetica Jaqueline Aranha. O conteudo foi derivado diretamente do '
    'codigo-fonte da aplicacao (Django 5.2), garantindo aderencia ao comportamento real '
    'implementado.')
h2('1.2 Escopo')
par('O sistema cobre o agendamento online publico (sem cadastro, identificado por '
    'telefone com verificacao OTP por SMS), o portal do profissional, o painel '
    'administrativo (agenda, clientes, prontuario, pacotes, promocoes, comissoes, NPS, '
    'notificacoes), alem de rotinas automaticas (lembretes, NPS, expiracao de pacotes, '
    'expurgo LGPD) e integracoes externas (SMS Zenvia, WhatsApp Meta, e-mail, Google '
    'Calendar, Cloudflare Turnstile).')
h2('1.3 Visao geral da arquitetura')
par('Aplicacao Django monolitica servida por gunicorn, com processamento assincrono via '
    'Celery (worker + beat), PostgreSQL como banco primario, Redis (broker, cache e '
    'sessao) e frontend em Vite/Tailwind com PWA. A regra de negocio reside em uma camada '
    'de servicos e eventos de dominio (EventBus); o estado do atendimento e governado por '
    'uma maquina de estados (FSM). Detalhes na secao 8.')

# ===================== 2. ATORES =====================
doc.add_page_break()
h1('2. Atores do Sistema')
par('Atores identificados a partir dos papeis de acesso e dos fluxos publicos/internos.')
add_table(
    ['Ator', 'Descricao'],
    [[a['nome'], a['descricao']] for a in D['casos-uso']['atores']],
    widths=[1.7, 5.0],
)

# ===================== 3. REQUISITOS FUNCIONAIS =====================
doc.add_page_break()
h1('3. Requisitos Funcionais')
add_table(
    ['ID', 'Ator', 'Descricao'],
    [[rf['id'], rf['ator'], rf['descricao']] for rf in D['regras-requisitos']['requisitos_funcionais']],
    widths=[0.6, 1.4, 4.7],
)

# ===================== 4. REQUISITOS NAO-FUNCIONAIS =====================
doc.add_page_break()
h1('4. Requisitos Nao-Funcionais')
add_table(
    ['ID', 'Categoria', 'Descricao'],
    [[r['id'], r['categoria'], r['descricao']] for r in D['regras-requisitos']['requisitos_nao_funcionais']],
    widths=[0.6, 1.2, 4.9],
)

# ===================== 5. REGRAS DE NEGOCIO =====================
doc.add_page_break()
h1('5. Regras de Negocio')
add_table(
    ['ID', 'Titulo', 'Descricao'],
    [[r['id'], r['titulo'], r['descricao']] for r in D['regras-requisitos']['regras']],
    widths=[0.6, 1.6, 4.5],
)

# ===================== 6. CASOS DE USO =====================
doc.add_page_break()
h1('6. Casos de Uso')
add_image('01-casos-uso-publico.png', 'Figura 1 — Diagrama de Casos de Uso (UML): Area Publica (Cliente).')
add_image('02-casos-uso-interno.png', 'Figura 2 — Diagrama de Casos de Uso (UML): Area Interna (Staff, Profissional, Sistema).')
h2('6.1 Especificacao dos casos de uso')
add_table(
    ['ID', 'Caso de Uso', 'Atores', 'Fluxo principal'],
    [[uc['id'], uc['nome'], ', '.join(uc['atores']), uc['fluxo']] for uc in D['casos-uso']['casos_de_uso']],
    widths=[0.5, 1.4, 1.5, 3.3],
)

# ===================== 7. MODELO DE DOMINIO =====================
doc.add_page_break()
h1('7. Modelo de Dominio (Diagrama de Classes)')
add_image('03-classe-agendamento.png', 'Figura 3 — Diagrama de Classes (UML): Nucleo de Agendamento.', paisagem=True)
add_image('04-classe-financeiro.png', 'Figura 4 — Diagrama de Classes (UML): Pacotes, Carteira, Fidelidade e Comissao.', paisagem=True)
add_image('05-classe-prontuario-lgpd.png', 'Figura 5 — Diagrama de Classes (UML): Prontuario, Anamnese, NPS, Termos, Acesso e Auditoria.', paisagem=True)
h2('7.1 Dicionario de entidades')
linhas = []
for grupo in ('models-core', 'models-aux'):
    for m in D[grupo]['models']:
        linhas.append([m['nome'], m.get('tabela', ''), m['descricao']])
add_table(['Entidade', 'Tabela', 'Descricao'], linhas, widths=[1.5, 1.4, 3.8])

# ===================== 8. DIAGRAMA DE ESTADOS =====================
doc.add_page_break()
h1('8. Diagrama de Estados — Atendimento (FSM)')
par('O status do atendimento e governado por uma maquina de estados finitos; transicoes '
    'invalidas levantam excecao tipada (Atendimento.TransicaoInvalida). Estado inicial: '
    f"{D['fsm']['estado_inicial']}.")
add_image('06-estados-atendimento.png', 'Figura 6 — Diagrama de Estados (UML): ciclo de vida do Atendimento.', paisagem=True)
h2('8.1 Tabela de transicoes')
trans = [[t['de'], t['para'], t['acao']] for t in D['fsm']['transicoes'] if t['para'] != '(nenhum)']
add_table(['Estado origem', 'Estado destino', 'Acao / gatilho'], trans, widths=[1.6, 1.6, 3.5])
h2('8.2 Efeitos colaterais reativos (signals / EventBus)')
for ef in D['fsm']['efeitos']:
    p = doc.add_paragraph(style='List Bullet')
    p.add_run(ef).font.size = Pt(9)

# ===================== 9. DIAGRAMA DE SEQUENCIA =====================
doc.add_page_break()
h1('9. Diagrama de Sequencia — Agendamento com OTP')
par('Fluxo do agendamento publico com o gate de verificacao OTP por SMS (anti-sequestro '
    'de cadastro), incluindo controle de concorrencia de slot e criacao transacional do '
    'atendimento.')
add_image('07-sequencia-agendamento-otp.png', 'Figura 7 — Diagrama de Sequencia (UML): agendamento publico com OTP.', paisagem=True)
h2('9.1 Passo a passo')
for passo in D['arquitetura']['fluxo_agendamento_otp']:
    p = doc.add_paragraph(style='List Number')
    p.add_run(passo).font.size = Pt(9)

# ===================== 10. ARQUITETURA =====================
doc.add_page_break()
h1('10. Arquitetura e Integracoes')
add_image('08-componentes-arquitetura.png', 'Figura 8 — Diagrama de Componentes / Implantacao (UML).', paisagem=True)
h2('10.1 Componentes')
add_table(['Componente', 'Papel'],
          [[c['nome'], c['papel']] for c in D['arquitetura']['componentes']],
          widths=[1.8, 4.9])
h2('10.2 Integracoes externas')
add_table(['Servico', 'Uso'],
          [[i['nome'], i['uso']] for i in D['arquitetura']['integracoes_externas']],
          widths=[1.8, 4.9])

# ===================== HISTORICO =====================
doc.add_page_break()
h1('Historico de Versoes')
add_table(['Versao', 'Data', 'Descricao'],
          [[VERSAO, DATA, 'Documento de requisitos + modelagem UML gerado a partir do codigo '
            '(pos-auditoria SWE de backend e migracao de frontend base_v2).']],
          widths=[0.9, 1.2, 4.6])

# ---- rodape com numero de pagina ----
for section in doc.sections:
    footer = section.footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run('Shiva Zen — Requisitos v' + VERSAO + '   |   Pag. ')
    run.font.size = Pt(8)
    fld1 = OxmlElement('w:fldChar'); fld1.set(qn('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText'); instr.set(qn('xml:space'), 'preserve'); instr.text = 'PAGE'
    fld2 = OxmlElement('w:fldChar'); fld2.set(qn('w:fldCharType'), 'end')
    run._r.append(fld1); run._r.append(instr); run._r.append(fld2)

out_docx = os.path.join(BASE, 'Requisitos-ShivaZen.docx')
doc.save(out_docx)
print('DOCX:', out_docx)

# ---- PDF ----
try:
    from docx2pdf import convert
    out_pdf = os.path.join(BASE, 'Requisitos-ShivaZen.pdf')
    convert(out_docx, out_pdf)
    print('PDF :', out_pdf)
except Exception as e:
    print('PDF falhou (gere abrindo o DOCX no Word > Salvar como PDF):', repr(e))
