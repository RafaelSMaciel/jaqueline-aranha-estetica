# -*- coding: utf-8 -*-
"""Base comum dos documentos do projeto (DOCX + PDF): estilo do TCC, capa, sumário, tabelas,
figuras UML, rodapé e um conversor do Markdown dos docs/ (títulos, parágrafos, listas, tabelas,
blocos de código e negrito/itálico/código/links em linha).

Usado por docs/gerar_documentacao.py (documento geral do TCC) e docs/gerar_especificacoes.py
(regras de negócio, requisitos e documento técnico). O PDF e o sumário saem pelo Word (Windows);
sem Word fica o DOCX (abra no Word, F9 no sumário e Salvar como PDF).
"""
import os
import re

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
# Versões de apresentação feitas no Lucidchart (mesmo conteúdo, exportadas para docs/uml/lucid/):
# quando existe a do Lucid, o documento usa ela; senão, a renderização do PlantUML.
LUCID = os.path.join(BASE, 'uml', 'lucid')

GOLD = RGBColor(0xC4, 0xA3, 0x5A)
DARK = RGBColor(0x4A, 0x34, 0x25)
CINZA = RGBColor(0x66, 0x66, 0x66)
CODIGO = RGBColor(0x7A, 0x3E, 0x1D)


def _shade(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:fill'), hexcolor)
    tcPr.append(shd)


def _sombrear_paragrafo(p, hexcolor):
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:fill'), hexcolor)
    pPr.append(shd)


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


# ─── Markdown em linha: **negrito**, *itálico*, `código`, [texto](link), ~~riscado~~ ──────────
_EM_LINHA = re.compile(r'(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]*\)|~~.+?~~|(?<![\w*])\*[^*\s][^*]*?\*(?![\w*]))')


def texto_em_linha(paragrafo, texto, size=None, negrito=False):
    """Escreve `texto` (Markdown em linha) no parágrafo, um run por trecho formatado."""
    texto = texto.replace('\\|', '|').replace('\\*', '*')
    for pedaco in _EM_LINHA.split(texto):
        if not pedaco:
            continue
        run_negrito, italico, codigo, riscado = negrito, False, False, False
        if pedaco.startswith('**') and pedaco.endswith('**') and len(pedaco) > 4:
            pedaco, run_negrito = pedaco[2:-2].replace('`', ''), True
        elif pedaco.startswith('`') and pedaco.endswith('`') and len(pedaco) > 2:
            pedaco, codigo = pedaco[1:-1], True
        elif pedaco.startswith('~~') and pedaco.endswith('~~'):
            pedaco, riscado = pedaco[2:-2], True
        elif pedaco.startswith('[') and '](' in pedaco:
            pedaco = pedaco[1:pedaco.index('](')]
            if len(pedaco) > 2 and pedaco.startswith('`') and pedaco.endswith('`'):
                pedaco, codigo = pedaco[1:-1], True
        elif pedaco.startswith('*') and pedaco.endswith('*') and len(pedaco) > 2:
            pedaco, italico = pedaco[1:-1], True
        run = paragrafo.add_run(pedaco)
        run.bold = run_negrito or None
        run.italic = italico or None
        run.font.strike = riscado or None
        if codigo:
            run.font.name = 'Consolas'
            run.font.color.rgb = CODIGO
            run.font.size = Pt((size or 10.5) - 1)
        elif size:
            run.font.size = Pt(size)


class Documento:
    def __init__(self, rotulo_rodape):
        self.rotulo_rodape = rotulo_rodape
        self.doc = Document()
        self.figuras = 0
        normal = self.doc.styles['Normal']
        normal.font.name = 'Calibri'
        normal.font.size = Pt(10.5)
        for lvl, sz in ((1, 16), (2, 13), (3, 11.5)):
            st = self.doc.styles[f'Heading {lvl}']
            st.font.color.rgb = DARK
            st.font.size = Pt(sz)
            st.font.name = 'Calibri'

    # ─── estrutura ───────────────────────────────────────────────────────────────
    def capa(self, titulo, subtitulo, versao, data):
        doc = self.doc
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
        r = tit.add_run(f'{titulo}\n{subtitulo}')
        r.bold = True
        r.font.size = Pt(16)
        for _ in range(6):
            doc.add_paragraph()
        meta = doc.add_paragraph()
        meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
        meta.add_run(f'Versão {versao}  |  {data}').font.size = Pt(11)

    def sumario(self):
        self.doc.add_page_break()
        titulo = self.doc.add_paragraph()
        r = titulo.add_run('Sumário')   # sem estilo de titulo: nao entra no proprio sumario
        r.bold = True
        r.font.size = Pt(16)
        r.font.color.rgb = DARK
        run = self.doc.add_paragraph().add_run()
        campo(run, 'TOC \\o "1-2" \\h \\z \\u', 'Atualize este campo no Word (Ctrl+A, F9) para gerar o sumário.')

    def _orientar(self, paisagem):
        """Garante a orientacao da pagina atual. Secao nova (= pagina nova) so quando ela muda."""
        sec = self.doc.sections[-1]
        if (sec.orientation == WD_ORIENT.LANDSCAPE) == paisagem:
            return False
        sec = self.doc.add_section(WD_SECTION.NEW_PAGE)
        sec.orientation = WD_ORIENT.LANDSCAPE if paisagem else WD_ORIENT.PORTRAIT
        sec.page_width, sec.page_height = sec.page_height, sec.page_width
        return True

    def h1(self, txt, nova_pagina=True):
        self._orientar(False)
        if nova_pagina:
            self.doc.add_page_break()
        self.doc.add_heading(txt, level=1)

    def h2(self, txt):
        self._orientar(False)
        self.doc.add_heading(txt, level=2)

    def h3(self, txt):
        self._orientar(False)
        self.doc.add_heading(txt, level=3)

    def par(self, txt, size=10.5):
        p = self.doc.add_paragraph()
        texto_em_linha(p, txt, size)
        return p

    def itens(self, lista, numerada=False, size=9.5):
        for item in lista:
            p = self.doc.add_paragraph(style='List Number' if numerada else 'List Bullet')
            texto_em_linha(p, item, size)

    def tabela(self, headers, rows, widths=None, size=9):
        t = self.doc.add_table(rows=1, cols=len(headers))
        t.style = 'Light Grid Accent 1'
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, h in enumerate(headers):
            cell = t.rows[0].cells[i]
            cell.text = ''
            run = cell.paragraphs[0].add_run(h)
            run.bold = True
            run.font.size = Pt(size + 0.5)
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            _shade(cell, '7A5F1F')
        for row in rows:
            cells = t.add_row().cells
            for i, val in enumerate(row):
                cells[i].text = ''
                texto_em_linha(cells[i].paragraphs[0], str(val), size)
        if widths:
            for col, w in enumerate(widths):
                for cell in t.columns[col].cells:
                    cell.width = Inches(w)
        self.doc.add_paragraph()
        return t

    def figura(self, caminho, legenda, descricao=None):
        """Diagrama numerado (docs/uml/lucid/<caminho> ou docs/uml/png/<caminho>); largo vai em
        paisagem, alto em retrato."""
        path = os.path.join(LUCID, caminho)
        if not os.path.exists(path):
            path = os.path.join(PNG, caminho)
        if not os.path.exists(path):
            self.doc.add_paragraph(f'[diagrama ausente: {caminho} — rode python docs/uml/gerar_uml.py]')
            return
        img = DocxImage.from_file(path)
        proporcao = img.px_height / img.px_width
        paisagem = proporcao <= 0.75
        if paisagem:
            if not self._orientar(True):
                self.doc.add_page_break()
        else:
            self._orientar(False)
        if descricao:
            self.par(descricao, size=9.5)
        sec = self.doc.sections[-1]
        max_w = sec.page_width - sec.left_margin - sec.right_margin
        max_h = sec.page_height - sec.top_margin - sec.bottom_margin - Inches(1.1 if descricao else 0.8)
        largura = min(max_w, int(max_h / proporcao))
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(path, width=largura)
        self.figuras += 1
        cap = self.doc.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = cap.add_run(f'Figura {self.figuras} — {legenda}')
        r.italic = True
        r.font.size = Pt(8.5)
        r.font.color.rgb = CINZA

    # ─── Markdown em bloco ───────────────────────────────────────────────────────
    def markdown(self, texto, niveis=None, renumerar=None, size=10):
        """Converte um trecho Markdown dos docs/ para o documento.

        niveis: {'##': 2, '###': 3} — nível do título no Word para cada marcador (o que não está
        no mapa vira parágrafo em negrito). renumerar: prefixo ('3') que troca o número do título
        do Markdown ("## 7. Faltas" -> "3.7 Faltas"), para encaixar a seção num capítulo.
        """
        niveis = niveis or {'##': 2, '###': 3}
        linhas = texto.splitlines()
        i, buffer = 0, []
        contador = [0]

        def despejar():
            if buffer:
                self.par(' '.join(s.strip() for s in buffer), size=size)
                buffer.clear()

        while i < len(linhas):
            linha = linhas[i]
            tira = linha.strip()
            if not tira:
                despejar()
                i += 1
                continue
            if tira.startswith('```'):
                despejar()
                bloco = []
                i += 1
                while i < len(linhas) and not linhas[i].strip().startswith('```'):
                    bloco.append(linhas[i])
                    i += 1
                i += 1
                self._codigo(bloco)
                continue
            m = re.match(r'^(#{1,6})\s+(.*)$', tira)
            if m:
                despejar()
                marcador, titulo = m.group(1), m.group(2).strip()
                titulo = re.sub(r'[*`]', '', titulo)
                if renumerar is not None and marcador == min(niveis, key=len):
                    contador[0] += 1
                    titulo = f'{renumerar}.{contador[0]} ' + re.sub(r'^\d+(\.\d+)*\.?\s+', '', titulo)
                nivel = niveis.get(marcador)
                if nivel == 1:
                    self.h1(titulo, nova_pagina=False)
                elif nivel == 2:
                    self.h2(titulo)
                elif nivel == 3:
                    self.h3(titulo)
                else:
                    p = self.doc.add_paragraph()
                    r = p.add_run(titulo)
                    r.bold = True
                i += 1
                continue
            if re.match(r'^-{3,}$|^\*{3,}$', tira):
                despejar()
                i += 1
                continue
            if tira.startswith('|'):
                despejar()
                tabela = []
                while i < len(linhas) and linhas[i].strip().startswith('|'):
                    tabela.append(linhas[i].strip())
                    i += 1
                self._tabela_md(tabela)
                continue
            if tira.startswith('>'):
                despejar()
                citacao = []
                while i < len(linhas) and linhas[i].strip().startswith('>'):
                    citacao.append(linhas[i].strip()[1:].strip())
                    i += 1
                p = self.doc.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.25)
                texto_em_linha(p, ' '.join(citacao), size - 0.5)
                for run in p.runs:
                    run.font.color.rgb = CINZA
                continue
            item = re.match(r'^(\s*)([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?(.*)$', linha)
            if item:
                despejar()
                recuo = len(item.group(1).replace('\t', '    '))
                numerada = item.group(2)[0].isdigit()
                marca = item.group(3)
                partes = [item.group(4)]
                i += 1
                # continuação: linha recuada que não é item, título, tabela nem código
                while i < len(linhas):
                    prox = linhas[i]
                    if not prox.strip() or re.match(r'^\s*([-*+]|\d+[.)])\s+', prox) \
                            or prox.strip().startswith(('|', '```', '#', '>')) or not prox.startswith(' '):
                        break
                    partes.append(prox.strip())
                    i += 1
                nivel = min(recuo // 2, 2)
                estilo = ('List Number' if numerada else 'List Bullet') + (f' {nivel + 1}' if nivel else '')
                p = self.doc.add_paragraph(style=estilo)
                if marca:
                    p.add_run('☑ ' if 'x' in marca.lower() else '☐ ').font.size = Pt(size - 0.5)
                texto_em_linha(p, ' '.join(partes), size - 0.5)
                continue
            buffer.append(linha)
            i += 1
        despejar()

    def _largura_util(self):
        """Largura útil da página atual, em polegadas."""
        sec = self.doc.sections[-1]
        return (sec.page_width - sec.left_margin - sec.right_margin) / 914400

    def _codigo(self, linhas):
        # Consolas ~0,55 em por caractere: a linha mais longa cabe na largura útil sem quebrar
        maior = max((len(linha.rstrip()) for linha in linhas), default=1)
        tamanho = max(5.5, min(7.5, self._largura_util() * 72 / (max(maior, 1) * 0.58)))
        for linha in linhas or ['']:
            p = self.doc.add_paragraph()
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.space_before = Pt(0)
            _sombrear_paragrafo(p, 'F4F1EA')
            r = p.add_run(linha.rstrip() or ' ')
            r.font.name = 'Consolas'
            r.font.size = Pt(tamanho)
        self.doc.add_paragraph()

    def _tabela_md(self, linhas):
        def celulas(linha):
            linha = linha.strip().strip('|')
            return [c.strip() for c in re.split(r'(?<!\\)\|', linha)]
        cab = celulas(linhas[0])
        corpo = [celulas(lin) for lin in linhas[1:] if not re.match(r'^\|?\s*:?-{2,}', lin.strip().strip('|').strip())]
        n = len(cab)
        corpo = [(c + [''] * n)[:n] for c in corpo]
        # largura proporcional ao texto, dentro da largura útil da página
        util = self._largura_util()
        pesos = [max([len(cab[j])] + [min(len(c[j]), 120) for c in corpo]) + 6 for j in range(n)]
        total = sum(pesos)
        larguras = [max(0.6, util * p / total) for p in pesos]
        escala = util / sum(larguras)
        self.tabela(cab, corpo, [w * escala for w in larguras], size=8.5)

    # ─── saída ───────────────────────────────────────────────────────────────────
    def _rodape(self):
        for i, section in enumerate(self.doc.sections):
            footer = section.footer
            if i > 0 and footer.is_linked_to_previous:
                continue
            fp = footer.paragraphs[0]
            fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = fp.add_run(self.rotulo_rodape + '   |   Pág. ')
            run.font.size = Pt(8)
            campo(run, 'PAGE')

    def salvar(self, nome_base, word=None):
        """Grava docs/<nome_base>.docx e, com o Word (COM), atualiza o sumário e exporta o PDF."""
        self._rodape()
        out_docx = os.path.join(BASE, nome_base + '.docx')
        out_pdf = os.path.join(BASE, nome_base + '.pdf')
        rascunho = os.path.join(BASE, f'~rascunho-{nome_base}.docx')
        self.doc.save(rascunho)
        if word is None:
            os.replace(rascunho, out_docx)
            print('DOCX:', out_docx, '(sumario e PDF pendentes: Word indisponivel)')
            return
        d = word.Documents.Open(rascunho, ReadOnly=False, AddToRecentFiles=False)
        try:
            for toc in d.TablesOfContents:
                toc.Update()
            d.SaveAs2(out_docx, FileFormat=16)            # wdFormatXMLDocument
            d.ExportAsFixedFormat(OutputFileName=out_pdf, ExportFormat=17,   # wdExportFormatPDF
                                  OptimizeFor=0, CreateBookmarks=1)          # marcadores = sumario do PDF
        finally:
            d.Close(False)
        os.remove(rascunho)
        print('DOCX:', out_docx)
        print('PDF :', out_pdf)


def abrir_word():
    """Word.Application (COM) ou None — sem Word, os documentos saem só em DOCX."""
    try:
        import win32com.client

        word = win32com.client.DispatchEx('Word.Application')
        word.Visible = False
        word.DisplayAlerts = 0
        return word
    except Exception as e:  # noqa: BLE001
        print('Word indisponivel:', repr(e))
        return None


def secao_md(caminho, titulo, nivel='##'):
    """Trecho do Markdown do título (regex no texto do título) até o próximo título de nível igual
    ou maior — sem a linha do título."""
    texto = open(caminho, encoding='utf-8').read()
    linhas = texto.splitlines()
    inicio = None
    for i, linha in enumerate(linhas):
        m = re.match(r'^(#{1,6})\s+(.*)$', linha)
        if not m:
            continue
        if inicio is None:
            if m.group(1) == nivel and re.search(titulo, m.group(2)):
                inicio = i + 1
        elif len(m.group(1)) <= len(nivel):
            return '\n'.join(linhas[inicio:i])
    if inicio is None:
        raise ValueError(f'secao {titulo!r} ({nivel}) nao encontrada em {caminho}')
    return '\n'.join(linhas[inicio:])
