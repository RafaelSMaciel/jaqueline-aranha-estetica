# -*- coding: utf-8 -*-
"""Planilha de custos operacionais (stack atual x recomendada, por cenário de volume).

Uma definição só (ABAS, abaixo) gera:
  - docs/tcc/ShivaZen-Custos-Operacionais.xlsx (openpyxl; no Windows o Excel recalcula e grava
    os valores, para quem abre a planilha sem recálculo ver os números);
  - com --json <pasta>: os valores (com fórmulas) e a formatação de cada aba em JSON, para montar
    a mesma planilha no Google Sheets.

Toda conta é fórmula sobre a aba Premissas (entradas em azul). Os preços e as premissas são os de
docs/CUSTOS-OPERACIONAIS.md: mudou um, mude os dois.

Uso (python do sistema, com openpyxl):
    python docs/tcc/gerar_planilha_custos.py [--json <pasta>] [--separador ";"]
"""
import argparse
import json
import os
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

TCC = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(TCC, 'ShivaZen-Custos-Operacionais.xlsx')

FONTE = 'Arial'
CAFE = '#1F1A14'          # cromo do painel (DESIGN-E-MARKETING.md)
AZUL = '#0000FF'          # entrada
VERDE = '#008000'         # ligação a outra aba
CINZA = '#BFBFBF'
REAIS = '"R$" #,##0.00;("R$" #,##0.00);"-"'
DOLARES = '"US$" #,##0.00;("US$" #,##0.00);"-"'
PORCENTO = '0.0%;(0.0%);"-"'
INTEIRO = '#,##0'
DECIMAL = '#,##0.00'
CONSULTA = '30/10/2026'

P = 'Premissas!'   # prefixo das referências à aba de entradas


def cabecalho(faixa):
    return {'range': faixa, 'bold': True, 'fg': '#FFFFFF', 'bg': CAFE, 'valign': 'middle', 'wrap': True}


def titulo(celula):
    return {'range': celula, 'bold': True, 'size': 14}


# ─── Premissas ──────────────────────────────────────────────────────────────────────
PREMISSAS = [
    ['Premissas da planilha de custos'],
    ['Células em azul são entradas: troque o valor e as outras abas recalculam. Preços de lista '
     f'consultados em {CONSULTA}.'],
    [],
    ['Câmbio', 'Valor', 'Fonte'],
    ['Dólar comercial (R$)', 5.10, 'PTAX de 16/10/2026 (R$ 5,08), arredondada para cima'],
    ['IOF em compra internacional no cartão', 0.035, 'Alíquota de 3,5% em vigor desde 2025'],
    ['Dólar efetivo (R$)', '=B5*(1+B6)', 'Usado em toda conversão de dólar'],
    [],
    ['Cenário', 'Muito pequena', 'Pequena', 'Média', 'Fonte'],
    ['Atendimentos por mês', 60, 150, 300, 'Estimativa para a clínica'],
    ['Clientes que aceitam e-mail de marketing', 80, 200, 400, 'Estimativa da base com consentimento'],
    ['Uso do Railway, produção (US$/mês)', 6.00, 6.40, 7.50,
     'Estimativa de consumo (docs/CUSTOS-OPERACIONAIS.md, seção 3)'],
    ['Uso do Railway, ambiente de testes (US$/mês)', 1.00, 1.00, 1.00,
     'Estimativa com o modo Serverless (dorme sem tráfego)'],
    [],
    ['Mensagens por atendimento', 'Quantidade', 'Fonte'],
    ['Código de acesso (OTP)', 1.0, 'Um código por pedido online ou acesso à área da cliente'],
    ['WhatsApp de utilidade', 1.25, 'Lembrete D-1 (~0,8) + NPS no máximo a cada 90 dias (~0,4) + aviso de vaga (~0,05)'],
    ['Códigos pelo SMS de reserva (variação com WhatsApp)', 0.10, 'Estimativa: quem não tem WhatsApp'],
    [],
    ['Preço', 'Valor', 'Unidade', 'Fonte'],
    ['Railway Hobby, mensalidade', 5, 'US$/mês', 'railway.com/pricing'],
    ['Railway Hobby, uso incluído na mensalidade', 5, 'US$/mês', 'railway.com/pricing'],
    ['Domínio .com.br', 40, 'R$/ano', 'Registro.br (via nuvemshop.com.br/blog/registro-br)'],
    ['Zenvia, pacote mínimo de SMS', 100, 'R$/mês', 'zenvia.com/zoe (até 1.000 SMS)'],
    ['Zenvia, preço por SMS no pacote mínimo', 0.10, 'R$', 'zenvia.com/zoe'],
    ['SMS pré-pago sem mensalidade (SMSGo, menor pacote)', 0.12, 'R$', 'smsgo.com.br (créditos não expiram)'],
    ['WhatsApp, mensagem de utilidade', 0.035, 'R$', 'Meta, tabela do Brasil desde 01/10/2026 (via zynfo.ai)'],
    ['WhatsApp, mensagem de autenticação', 0.035, 'R$', 'Meta, mesma tarifa da utilidade (via authgear.com)'],
    ['Resend Free, limite diário', 100, 'e-mails/dia', 'resend.com/pricing'],
    ['Resend Pro', 20, 'US$/mês', 'resend.com/pricing'],
    ['Brevo Free, limite diário', 300, 'e-mails/dia', 'Resenha de terceiro (sender.net); confirmar em brevo.com'],
    ['Brevo Starter', 9, 'US$/mês', 'Resenha de terceiro (sender.net); confirmar em brevo.com'],
    ['Backup (R2), Sentry, healthchecks, Turnstile e push', 0, 'R$/mês', 'Planos gratuitos'],
]
FMT_PREMISSAS = {
    'rules': [
        titulo('A1'),
        {'range': 'A2', 'italic': True},
        cabecalho('A4:C4'), cabecalho('A9:E9'), cabecalho('A15:C15'), cabecalho('A20:D20'),
        {'range': 'B5', 'fg': AZUL, 'number': DECIMAL},
        {'range': 'B6', 'fg': AZUL, 'number': PORCENTO},
        {'range': 'B7', 'number': '"R$" #,##0.0000'},
        {'range': 'B10:D11', 'fg': AZUL, 'number': INTEIRO},
        {'range': 'B12:D13', 'fg': AZUL, 'number': DOLARES},
        {'range': 'B16:B17', 'fg': AZUL, 'number': DECIMAL},
        {'range': 'B18', 'fg': AZUL, 'number': PORCENTO},
        {'range': 'B21:B22', 'fg': AZUL, 'number': DOLARES},
        {'range': 'B23:B24', 'fg': AZUL, 'number': REAIS},
        {'range': 'B25:B28', 'fg': AZUL, 'number': '"R$" #,##0.000'},
        {'range': 'B29', 'fg': AZUL, 'number': INTEIRO},
        {'range': 'B30', 'fg': AZUL, 'number': DOLARES},
        {'range': 'B31', 'fg': AZUL, 'number': INTEIRO},
        {'range': 'B32', 'fg': AZUL, 'number': DOLARES},
        {'range': 'B33', 'fg': AZUL, 'number': REAIS},
        {'range': 'A4:C7', 'borders': CINZA}, {'range': 'A9:E13', 'borders': CINZA},
        {'range': 'A15:C18', 'borders': CINZA}, {'range': 'A20:D33', 'borders': CINZA},
    ],
    'col_widths': {'A': 430, 'B': 110, 'C': 110, 'D': 110, 'E': 330},
}

# ─── Custo mensal ───────────────────────────────────────────────────────────────────
CENARIOS = 'BCD'   # colunas dos cenários aqui e em Premissas


def por_cenario(rotulo, formula):
    """Linha com a mesma fórmula nas três colunas de cenário; '{c}' vira a coluna do cenário."""
    return [rotulo] + [formula.format(c=c) for c in CENARIOS]


RAILWAY = (f'=({P}$B$21+MAX(0,{P}{{c}}12+{P}{{c}}13-{P}$B$22))*{P}$B$7')
CUSTO_MENSAL = [
    ['Custo mensal por cenário (R$)'],
    ['Fórmulas sobre a aba Premissas. Para simular, mude as entradas em azul de lá.'],
    [],
    por_cenario('Stack atual (como o código está hoje)', f'={P}{{c}}9'),
    por_cenario('Atendimentos por mês', f'={P}{{c}}10'),
    por_cenario('Railway Hobby (produção + testes)', RAILWAY),
    por_cenario('Domínio (anual ÷ 12)', f'={P}$B$23/12'),
    por_cenario('SMS Zenvia (pacote mínimo mensal)', f'=MAX({P}$B$24,{P}{{c}}10*{P}$B$16*{P}$B$25)'),
    por_cenario('WhatsApp de utilidade', f'={P}{{c}}10*{P}$B$17*{P}$B$27'),
    por_cenario('E-mail Resend (Pro quando a campanha passa de 100 por dia)',
                f'=IF({P}{{c}}11>{P}$B$29,{P}$B$30*{P}$B$7,0)'),
    por_cenario('Backup, monitoramento, antirrobô e push', f'={P}$B$33'),
    por_cenario('Total da stack atual', '=SUM({c}6:{c}11)'),
    [],
    por_cenario('Stack recomendada', f'={P}{{c}}9'),
    por_cenario('Railway Hobby (produção + testes)', '={c}6'),
    por_cenario('Domínio (anual ÷ 12)', '={c}7'),
    por_cenario('SMS pré-pago sem mensalidade', f'={P}{{c}}10*{P}$B$16*{P}$B$26'),
    por_cenario('WhatsApp de utilidade', '={c}9'),
    por_cenario('E-mail Brevo (Free; Starter quando a campanha passa de 300 por dia)',
                f'=IF({P}{{c}}11>{P}$B$31,{P}$B$32*{P}$B$7,0)'),
    por_cenario('Backup, monitoramento, antirrobô e push', f'={P}$B$33'),
    por_cenario('Total da stack recomendada', '=SUM({c}15:{c}20)'),
    [],
    por_cenario('Variação: código de acesso pelo WhatsApp', f'={P}{{c}}9'),
    por_cenario('Código pelo WhatsApp (autenticação)', f'={P}{{c}}10*{P}$B$16*(1-{P}$B$18)*{P}$B$28'),
    por_cenario('SMS de reserva (pré-pago)', f'={P}{{c}}10*{P}$B$16*{P}$B$18*{P}$B$26'),
    por_cenario('Total com o código pelo WhatsApp', '={c}21-{c}17+{c}24+{c}25'),
]
FMT_CUSTO = {
    'rules': [
        titulo('A1'), {'range': 'A2', 'italic': True},
        cabecalho('A4:D4'), cabecalho('A14:D14'), cabecalho('A23:D23'),
        {'range': 'B4:D4', 'fg': '#FFFFFF', 'align': 'right'},
        {'range': 'B14:D14', 'align': 'right'}, {'range': 'B23:D23', 'align': 'right'},
        {'range': 'B5:D5', 'fg': VERDE, 'number': INTEIRO},
        {'range': 'B6:D12', 'number': REAIS}, {'range': 'B15:D21', 'number': REAIS},
        {'range': 'B24:D26', 'number': REAIS},
        {'range': 'B11:D11', 'fg': VERDE}, {'range': 'B20:D20', 'fg': VERDE},
        {'range': 'A12:D12', 'bold': True, 'bg': '#F1EFE8'},
        {'range': 'A21:D21', 'bold': True, 'bg': '#F1EFE8'},
        {'range': 'A26:D26', 'bold': True, 'bg': '#F1EFE8'},
        {'range': 'A4:D12', 'borders': CINZA}, {'range': 'A14:D21', 'borders': CINZA},
        {'range': 'A23:D26', 'borders': CINZA},
    ],
    'col_widths': {'A': 430, 'B:D': 130},
}

# ─── Resumo ─────────────────────────────────────────────────────────────────────────
CM = "'Custo mensal'!"


def linha_resumo(i, c):
    """Linha i do resumo para o cenário da coluna c (B, C ou D) das outras abas."""
    return [f'={P}{c}9', f'={P}{c}10', f'={CM}{c}12', f'={CM}{c}21', f'=C{i}-D{i}',
            f'=IF(C{i}=0,0,E{i}/C{i})', f'=IF(B{i}=0,0,D{i}/B{i})', f'=D{i}*12', f'={CM}{c}26']


RESUMO = [
    ['Custo mensal do sistema: stack atual x recomendada'],
    ['Trocar o SMS da Zenvia por um pré-pago e o e-mail pelo Brevo Free reduz o custo a menos da '
     'metade nos cenários pequenos.'],
    [],
    ['Cenário', 'Atendimentos por mês', 'Stack atual (R$/mês)', 'Stack recomendada (R$/mês)',
     'Economia (R$/mês)', 'Economia (%)', 'Recomendada por atendimento (R$)',
     'Recomendada por ano (R$)', 'Com código pelo WhatsApp (R$/mês)'],
    linha_resumo(5, 'B'),
    linha_resumo(6, 'C'),
    linha_resumo(7, 'D'),
    [],
    ['O que a stack recomendada exige:'],
    ['1. SMS: um adaptador para o provedor pré-pago no módulo utils/sms (hoje ele só fala com a Zenvia).'],
    ['2. E-mail: só configuração (EMAIL_BACKEND do Brevo no anymail e a chave BREVO_API_KEY).'],
    ['3. Código pelo WhatsApp (opcional): um canal novo no OTP e um template de autenticação aprovado na Meta.'],
    [],
    ['Os valores de infraestrutura são estimativas de consumo. Custos de implantação e fontes nas abas próprias.'],
]
FMT_RESUMO = {
    'rules': [
        titulo('A1'), {'range': 'A2', 'bold': True},
        cabecalho('A4:I4'),
        {'range': 'A5:B7', 'fg': VERDE}, {'range': 'B5:B7', 'number': INTEIRO},
        {'range': 'C5:D7', 'fg': VERDE, 'number': REAIS},
        {'range': 'E5:E7', 'number': REAIS}, {'range': 'F5:F7', 'number': PORCENTO},
        {'range': 'G5:H7', 'number': REAIS}, {'range': 'I5:I7', 'fg': VERDE, 'number': REAIS},
        {'range': 'D5:D7', 'bold': True},
        {'range': 'A4:I7', 'borders': CINZA},
        {'range': 'A9', 'bold': True}, {'range': 'A14', 'italic': True},
    ],
    'col_widths': {'A': 150, 'B:I': 130},
    'row_heights': {'4': 48},
}

# ─── Stack recomendada ─────────────────────────────────────────────────────────────
STACK = [
    ['Ferramentas recomendadas para uma clínica pequena'],
    ['Critério: menor custo fixo, sem servidor para administrar e sem pagar por capacidade que a clínica não usa.'],
    [],
    ['Função', 'Ferramenta', 'Plano', 'Custo de referência', 'Muda o código?', 'Por quê', 'Alternativa considerada'],
    ['Hospedagem do site, do painel e dos jobs', 'Railway', 'Hobby', 'US$ 5/mês com US$ 5 de uso; o excedente é por uso',
     'Não (já configurado)', 'Deploy por branch, HTTPS e cron sem administrar servidor',
     'VPS própria (Hetzner, ~€ 5,49/mês): custo parecido e a clínica passa a cuidar do servidor'],
    ['Banco de dados', 'PostgreSQL 18 no Railway', 'Incluído no uso', 'Entra no uso do Railway', 'Não',
     'Na mesma rede do app, com volume próprio', 'Neon grátis: banco fora da rede do app e demora para acordar'],
    ['Domínio e DNS', 'Registro.br', 'Anual', 'R$ 40/ano; DNS sem custo', 'Não', 'Domínio .com.br com DNS incluído', 'Outros registradores: mesmo .com.br, mais caro'],
    ['Código de acesso da cliente (OTP)', 'SMS pré-pago (SMSGo)', 'Créditos que não expiram', 'R$ 0,12 por SMS no menor pacote',
     'Sim: adaptador em utils/sms', 'Com pouco volume, o pacote mínimo da Zenvia (R$ 100/mês) fica quase todo sem uso',
     'Código pelo WhatsApp (R$ 0,035): mais barato, mas exige template de autenticação e reserva por SMS'],
    ['Lembrete D-1, NPS e aviso de vaga', 'WhatsApp Cloud API (Meta, direto)', 'Por mensagem', 'R$ 0,035 por mensagem de utilidade', 'Não',
     'Sem intermediário: pela Zenvia o mesmo template custa R$ 0,47 a R$ 0,55', 'Lembrete só por e-mail: grátis, mas evita menos faltas'],
    ['E-mail (transacional e campanhas)', 'Brevo', 'Free', '300 e-mails por dia sem custo', 'Não (configuração do anymail)',
     'A campanha para até ~300 clientes sai num dia só, sem mudar código',
     'Resend Free (100/dia) com campanha em lotes (exige código) ou Resend Pro (US$ 20/mês)'],
    ['Backup do banco', 'Cloudflare R2', 'Gratuito até 10 GB', 'R$ 0', 'Não', 'Os dumps cifrados da clínica cabem na cota gratuita', '-'],
    ['Erros da aplicação', 'Sentry', 'Developer', 'R$ 0 (5 mil erros/mês)', 'Não', 'Alerta de erro com filtro de dado pessoal já configurado', '-'],
    ['Monitor dos jobs', 'healthchecks.io', 'Hobbyist', 'R$ 0 (20 checks)', 'Não', 'Avisa se uma rotina parar de rodar', '-'],
    ['Antirrobô nos formulários', 'Cloudflare Turnstile', 'Gratuito', 'R$ 0', 'Não', 'Protege o envio de SMS contra robô', '-'],
    ['Avisos à equipe', 'Web Push (VAPID)', 'Gratuito', 'R$ 0', 'Não', 'Notificação no celular sem SMS nem WhatsApp', '-'],
]
FMT_STACK = {
    'rules': [titulo('A1'), {'range': 'A2', 'italic': True}, cabecalho('A4:G4'),
              {'range': 'A5:G15', 'wrap': True, 'valign': 'top'}, {'range': 'A5:A15', 'bold': True},
              {'range': 'A4:G15', 'borders': CINZA}],
    'col_widths': {'A': 190, 'B': 170, 'C': 130, 'D': 190, 'E': 150, 'F': 290, 'G': 300},
}

# ─── Custos únicos ──────────────────────────────────────────────────────────────────
UNICOS = [
    ['Custos de implantação (uma vez)'],
    [],
    ['Item', 'Quantidade', 'Preço unitário (R$)', 'Valor (R$)', 'Observação'],
    ['Domínio .com.br (1º ano)', 1, f'={P}B23', '=B4*C4', 'Pago à vista; no custo mensal aparece dividido por 12'],
    ['Chip pré-pago para o número do WhatsApp da API', 1, 20, '=B5*C5',
     'Estimativa. O número não pode estar em uso no aplicativo WhatsApp comum'],
    ['Primeiro crédito de SMS pré-pago', 300, f'={P}B26', '=B6*C6', 'Créditos que não expiram'],
    ['Verificação do negócio na Meta', 1, 0, '=B7*C7', 'Sem custo; exige CNPJ'],
    ['Contas no Railway, Brevo, Cloudflare, Sentry e healthchecks', 1, 0, '=B8*C8', 'Sem custo; o Railway pede cartão'],
    ['Total', None, None, '=SUM(D4:D8)', None],
]
FMT_UNICOS = {
    'rules': [titulo('A1'), cabecalho('A3:E3'),
              {'range': 'B4:B8', 'number': INTEIRO}, {'range': 'B6', 'fg': AZUL},
              {'range': 'C4', 'fg': VERDE, 'number': REAIS}, {'range': 'C6', 'fg': VERDE, 'number': '"R$" #,##0.000'},
              {'range': 'C5', 'fg': AZUL, 'bg': '#FFFF00', 'number': REAIS},
              {'range': 'C7:C8', 'fg': AZUL, 'number': REAIS},
              {'range': 'D4:D9', 'number': REAIS}, {'range': 'A9:E9', 'bold': True, 'bg': '#F1EFE8'},
              {'range': 'A3:E9', 'borders': CINZA}],
    'col_widths': {'A': 380, 'B': 100, 'C': 140, 'D': 120, 'E': 420},
}

# ─── Fontes ─────────────────────────────────────────────────────────────────────────
FONTES = [
    ['Fontes'],
    [],
    ['Item', 'Fonte', 'Endereço', 'Consultado em'],
    ['Railway: planos e tarifas', 'Railway', 'https://railway.com/pricing', CONSULTA],
    ['Railway: modo Serverless', 'Railway Docs', 'https://docs.railway.com/reference/app-sleeping', CONSULTA],
    ['WhatsApp: modelo de cobrança', 'Meta for Developers', 'https://developers.facebook.com/docs/whatsapp/pricing', CONSULTA],
    ['WhatsApp: tarifas do Brasil (01/10/2026)', 'Tabela da Meta reproduzida por terceiro', 'https://zynfo.ai/whatsapp-pricing/brazil', CONSULTA],
    ['WhatsApp: tarifa de autenticação', 'Terceiro', 'https://www.authgear.com/post/whatsapp-api-pricing', CONSULTA],
    ['Zenvia: pacotes de SMS', 'Zenvia', 'https://zenvia.com/zoe/', CONSULTA],
    ['SMS pré-pago sem mensalidade', 'SMSGo', 'https://smsgo.com.br/', CONSULTA],
    ['SMS para OTP no Brasil: comparativo', 'Terceiro', 'https://x-apps.com.br/sms-verificacao-otp-brasil-provedores-precos/', CONSULTA],
    ['Resend: planos', 'Resend', 'https://resend.com/pricing', CONSULTA],
    ['Brevo: plano gratuito e Starter', 'Resenha de terceiro', 'https://www.sender.net/reviews/brevo/pricing/free-plan', CONSULTA],
    ['Domínio .com.br', 'Terceiro', 'https://www.nuvemshop.com.br/blog/registro-br', CONSULTA],
    ['Sentry: plano Developer', 'Terceiro', 'https://costbench.com/software/developer-tools/sentry/free-plan/', CONSULTA],
    ['healthchecks.io: planos', 'healthchecks.io', 'https://healthchecks.io/pricing/', CONSULTA],
    ['Cloudflare R2: cota gratuita', 'Cloudflare Docs', 'https://developers.cloudflare.com/r2/pricing', CONSULTA],
    ['Câmbio: PTAX de 16/10/2026', 'Terceiro', 'https://www.simianx.ai/pt/stories/us-dollar-exchange-rate-by-year-every-year-1971-2026-table', CONSULTA],
    ['Câmbio: cotação oficial', 'Banco Central', 'https://www.bcb.gov.br/estabilidadefinanceira/historicocotacoes', CONSULTA],
    ['Premissas de volume e consumo', 'Projeto', 'docs/CUSTOS-OPERACIONAIS.md', CONSULTA],
]
FMT_FONTES = {
    'rules': [titulo('A1'), cabecalho('A3:D3'), {'range': 'A3:D20', 'borders': CINZA}],
    'col_widths': {'A': 300, 'B': 240, 'C': 520, 'D': 110},
}

ABAS = [
    ('Resumo', RESUMO, FMT_RESUMO),
    ('Custo mensal', CUSTO_MENSAL, FMT_CUSTO),
    ('Stack recomendada', STACK, FMT_STACK),
    ('Premissas', PREMISSAS, FMT_PREMISSAS),
    ('Custos únicos', UNICOS, FMT_UNICOS),
    ('Fontes', FONTES, FMT_FONTES),
]


# ─── xlsx ───────────────────────────────────────────────────────────────────────────
def _celulas(faixa):
    """'B5:D7' -> [(linha, coluna), ...] (1-based)."""
    m = re.match(r'^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$', faixa)
    c1, l1, c2, l2 = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4) or m.group(2))

    def col(letras):
        n = 0
        for ch in letras:
            n = n * 26 + ord(ch) - 64
        return n
    return [(lin, c) for lin in range(l1, l2 + 1) for c in range(col(c1), col(c2) + 1)]


def _hex(cor):
    return cor.lstrip('#').upper()


def _aplicar(ws, fmt):
    lado = Side(style='thin', color=None)
    for regra in fmt['rules']:
        for lin, col in _celulas(regra['range']):
            cel = ws.cell(row=lin, column=col)
            f = cel.font
            cel.font = Font(name=FONTE, size=regra.get('size', f.size or 10),
                            bold=regra.get('bold', f.bold), italic=regra.get('italic', f.italic),
                            color=_hex(regra['fg']) if 'fg' in regra else f.color)
            if 'bg' in regra:
                cel.fill = PatternFill('solid', fgColor=_hex(regra['bg']))
            if 'number' in regra:
                cel.number_format = regra['number']
            if any(k in regra for k in ('align', 'valign', 'wrap')):
                a = cel.alignment
                cel.alignment = Alignment(horizontal=regra.get('align', a.horizontal),
                                          vertical={'middle': 'center'}.get(regra.get('valign'), regra.get('valign', a.vertical)),
                                          wrap_text=regra.get('wrap', a.wrap_text))
            if 'borders' in regra:
                lado = Side(style='thin', color=_hex(regra['borders']))
                cel.border = Border(left=lado, right=lado, top=lado, bottom=lado)
    for faixa, px in fmt.get('col_widths', {}).items():
        a, _, b = faixa.partition(':')
        b = b or a
        for n in range(ord(a) - 64, ord(b) - 64 + 1):
            ws.column_dimensions[get_column_letter(n)].width = round(px / 7, 1)
    for linha, px in fmt.get('row_heights', {}).items():
        ws.row_dimensions[int(linha)].height = px * 0.75


def gerar_xlsx():
    wb = Workbook()
    wb.remove(wb.active)
    for nome, linhas, fmt in ABAS:
        ws = wb.create_sheet(nome)
        for i, linha in enumerate(linhas, start=1):
            for j, valor in enumerate(linha, start=1):
                if valor is not None:
                    ws.cell(row=i, column=j, value=valor).font = Font(name=FONTE, size=10)
        _aplicar(ws, fmt)
        ws.sheet_view.showGridLines = False
    wb.save(SAIDA)
    return SAIDA


def recalcular_no_excel(caminho):
    """Abre no Excel (COM), recalcula e salva: o arquivo passa a ter os valores das fórmulas."""
    try:
        import win32com.client
    except ImportError:
        print('Excel indisponivel: o arquivo sai sem valores calculados (recalcula ao abrir)')
        return
    excel = win32com.client.DispatchEx('Excel.Application')
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        wb = excel.Workbooks.Open(os.path.abspath(caminho))
        excel.CalculateFull()
        wb.Save()
        wb.Close(False)
    finally:
        excel.Quit()


# ─── Google Sheets ──────────────────────────────────────────────────────────────────
def _para_sheets(valor, separador):
    if isinstance(valor, str):
        if valor.startswith('='):
            return valor.replace(',', separador) if separador != ',' else valor
        if re.match(r'^\d{2}/\d{2}/\d{4}$', valor):
            return "'" + valor          # data como texto
    return valor


def exportar_json(pasta, separador):
    os.makedirs(pasta, exist_ok=True)
    for i, (nome, linhas, fmt) in enumerate(ABAS):
        largura = max(len(linha) for linha in linhas)
        grade = [[_para_sheets(v, separador) for v in linha] + [None] * (largura - len(linha)) for linha in linhas]
        with open(os.path.join(pasta, f'{i}-valores.json'), 'w', encoding='utf-8') as f:
            json.dump({'aba': nome, 'valores': grade}, f, ensure_ascii=False)
        spec = {'rules': [dict(r, font=FONTE) for r in fmt['rules']], 'col_widths': fmt.get('col_widths', {})}
        if 'row_heights' in fmt:
            spec['row_heights'] = fmt['row_heights']
        with open(os.path.join(pasta, f'{i}-formato.json'), 'w', encoding='utf-8') as f:
            json.dump(spec, f, ensure_ascii=False)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', help='pasta para os JSON do Google Sheets')
    ap.add_argument('--separador', default=',', help='separador de argumentos das fórmulas no Sheets')
    args = ap.parse_args()
    caminho = gerar_xlsx()
    recalcular_no_excel(caminho)
    print('XLSX:', caminho)
    if args.json:
        exportar_json(args.json, args.separador)
        print('JSON:', args.json)
