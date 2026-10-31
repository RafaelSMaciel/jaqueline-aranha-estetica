# -*- coding: utf-8 -*-
"""Gera docs/tcc/JaquelineAranhaEstetica-Requisitos.pdf: requisitos funcionais e não funcionais em
linguagem simples, preto e branco, sem cores nem enfeites (para anexar ao TCC).

Os códigos (RF01..RF47, RNF01..RNF24) são os mesmos de _dados_requisitos.json, que guarda a versão
técnica de cada requisito e como ele é verificado. Mudou um requisito lá? Ajuste a frase aqui também.

Uso: python docs/geradores/gerar_requisitos.py   (precisa do Word no Windows para exportar o PDF)
"""
import os
import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

AQUI = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(os.path.dirname(AQUI), 'tcc', 'JaquelineAranhaEstetica-Requisitos.pdf')

FUNCIONAIS = [
    ('RF01', 'Mostrar no site os serviços, os preços, as promoções, a equipe, a galeria de fotos e os depoimentos '
             'autorizados pelas clientes.'),
    ('RF02', 'Permitir que a cliente agende pelo site escolhendo o procedimento, a profissional, o dia e o horário, '
             'confirmando o celular com um código recebido por SMS. Cada cliente pode ter até três horários futuros '
             'marcados pelo site.'),
    ('RF03', 'Durante o agendamento, pedir que a cliente aceite a Política de Privacidade e os termos do procedimento, '
             'autorize o uso das suas informações de saúde, escolha como quer receber avisos e responda a ficha de '
             'anamnese, quando o procedimento tiver uma.'),
    ('RF04', 'Oferecer a área "Meus agendamentos", acessada com o celular ou o e-mail cadastrado e um código enviado '
             'ao celular, onde a cliente vê os atendimentos marcados e os já realizados, os retornos e o seu código '
             'de indicação.'),
    ('RF05', 'Permitir remarcar um atendimento pela área da cliente até 24 horas antes, escolhendo outro horário livre.'),
    ('RF06', 'Permitir cancelar um atendimento pela área da cliente, avisando que o cancelamento feito com menos de '
             '24 horas de antecedência conta como falta.'),
    ('RF07', 'Permitir confirmar a presença ou cancelar pelo lembrete enviado na véspera, por WhatsApp ou e-mail.'),
    ('RF08', 'Permitir aceitar, por um link, os termos que ficaram pendentes para um atendimento.'),
    ('RF09', 'Permitir entrar na lista de espera sem fazer cadastro, informando nome, celular, procedimento, dia e '
             'turno desejados, e avisar a cliente quando surgir uma vaga.'),
    ('RF10', 'Permitir que a cliente dê uma nota de 0 a 10 ao atendimento e autorize a publicação do seu depoimento '
             'no site.'),
    ('RF11', 'Permitir responder a ficha de anamnese por um link, junto com a autorização para o uso das '
             'informações de saúde.'),
    ('RF12', 'Permitir que a cliente baixe uma cópia dos seus dados, deixe de receber promoções e pesquisas por um '
             'link e peça para não receber mais mensagens de WhatsApp respondendo "PARAR".'),
    ('RF13', 'Indicar o WhatsApp da clínica como outro caminho para agendar quando o envio de SMS não estiver '
             'funcionando.'),
    ('RF14', 'Permitir que a equipe entre no sistema com e-mail e senha e uma segunda confirmação por um aplicativo '
             'no celular (obrigatória para a administradora e para as profissionais), e recupere a senha por link.'),
    ('RF15', 'Permitir aprovar ou recusar os pedidos de agendamento feitos pelo site, um a um ou vários de uma vez, '
             'avisando a cliente por e-mail.'),
    ('RF16', 'Permitir agendar pela recepção, para uma cliente já cadastrada ou nova, usando os mesmos horários '
             'livres do site e enviando a confirmação à cliente.'),
    ('RF17', 'Permitir organizar a agenda: ver os atendimentos de cada dia, filtrar, mudar a situação de cada um '
             '(confirmado, realizado, faltou), arrastar um atendimento para outro horário no calendário e registrar '
             'o valor cobrado.'),
    ('RF18', 'Oferecer às profissionais um espaço próprio com a agenda do dia e da semana, os pedidos para aprovar, '
             'a marcação de atendimento realizado, as anotações da sessão, os alertas de saúde das clientes e avisos '
             'no celular.'),
    ('RF19', 'Permitir registrar e consultar o prontuário da cliente, guardando as versões anteriores, e anotar o '
             'que foi feito em cada sessão.'),
    ('RF20', 'Permitir cadastrar e consultar clientes, com busca pelo nome, alertas de saúde, pacotes, histórico de '
             'atendimentos e autorizações de contato, sem aceitar telefone, CPF ou e-mail repetidos.'),
    ('RF21', 'Permitir cadastrar as profissionais com os seus horários de trabalho, folgas e bloqueios, e cadastrar '
             'os procedimentos, os preços (com a data em que passam a valer) e quais profissionais fazem cada um.'),
    ('RF22', 'Permitir criar pacotes de sessões, vender um pacote a uma cliente, acompanhar as sessões que restam e '
             'a validade, cancelar a compra devolvendo o valor e prorrogar a validade.'),
    ('RF23', 'Permitir criar promoções e enviar o e-mail da promoção, uma única vez, às clientes que autorizaram '
             'receber esse tipo de mensagem.'),
    ('RF24', 'Mostrar um resumo da clínica e o financeiro de cada mês: faturamento, vendas de pacotes, devoluções, '
             'faltas, procedimentos e profissionais com mais atendimentos e as comissões a pagar.'),
    ('RF25', 'Permitir acompanhar as notas dadas pelas clientes e escolher quais depoimentos aparecem no site.'),
    ('RF26', 'Mostrar a lista de espera e permitir avisar uma cliente quando abrir uma vaga.'),
    ('RF27', 'Permitir montar as fichas de anamnese, as pesquisas e as perguntas do prontuário, publicar novas '
             'versões dos termos e acompanhar quem já os aceitou.'),
    ('RF28', 'Permitir cadastrar os usuários da equipe e ajustar as regras de funcionamento (por exemplo, quantas '
             'faltas pausam o agendamento pelo site), os contatos e a identidade visual do site.'),
    ('RF29', 'Executar sozinho, todos os dias, as tarefas de rotina: enviar os e-mails pendentes, o lembrete da '
             'véspera, a pesquisa de satisfação, o aviso de pacote perto de vencer e a mensagem de aniversário, e '
             'apagar os dados que já passaram do prazo de guarda.'),
    ('RF30', 'Indicar quando um procedimento tem retorno gratuito e em que período ele deve acontecer, para a '
             'recepção combinar a data com a cliente.'),
    ('RF31', 'Ao concluir um atendimento, cancelá-lo ou registrar uma falta, atualizar sozinho as sessões do pacote, '
             'a comissão da profissional, o crédito da cliente, a contagem de faltas e o aviso à lista de espera.'),
    ('RF32', 'Receber as respostas que as clientes enviam pelo WhatsApp (nota da pesquisa e pedido de "PARAR") e a '
             'confirmação de entrega dos SMS, conferindo que vieram mesmo desses serviços.'),
    ('RF33', 'Mostrar na ficha da cliente como ela autorizou ser contatada e permitir mudar isso a pedido dela, '
             'guardando o histórico.'),
    ('RF34', 'Permitir que a profissional acompanhe a própria agenda no calendário do celular.'),
    ('RF35', 'Guardar os e-mails a enviar numa fila e tentar de novo, algumas vezes, quando o envio falhar.'),
    ('RF36', 'Registrar sozinho quem alterou cada informação importante, quando e de onde.'),
    ('RF37', 'Permitir definir as regras de comissão: um percentual ou um valor fixo, por profissional e/ou por '
             'procedimento.'),
    ('RF38', 'Permitir que a profissional consulte as próprias comissões do mês, sem ver o nome das clientes.'),
    ('RF39', 'Permitir que a cliente mude, na sua área, como prefere receber promoções, lembretes e pesquisas.'),
    ('RF40', 'Permitir que a cliente retire a autorização de uso das suas informações de saúde.'),
    ('RF41', 'Permitir que a cliente peça a correção ou a exclusão dos seus dados; o pedido chega à clínica com '
             'prazo de 15 dias para resposta.'),
    ('RF42', 'Permitir que a cliente baixe os seus dados pela sua área, depois de confirmar o código recebido no '
             'celular.'),
    ('RF43', 'Oferecer um formulário de contato no site, protegido contra envios automáticos; se o e-mail não puder '
             'ser enviado, a mensagem não se perde e o WhatsApp é sugerido.'),
    ('RF44', 'Mostrar a Política de Privacidade sempre na versão em vigor, com a identificação da clínica e da '
             'pessoa responsável pelos dados.'),
    ('RF45', 'Registrar quando alguém da equipe consulta os alertas de saúde das clientes.'),
    ('RF46', 'Mostrar no site apenas números reais (nota média, clientes atendidas, tratamentos e anos de '
             'experiência), escondendo os que ainda não têm informação suficiente.'),
    ('RF47', 'Mostrar uma página de perguntas frequentes separada por assunto (agendamento, pagamentos e pacotes, '
             'tratamentos, privacidade), com as cinco principais na página inicial.'),
]

NAO_FUNCIONAIS = [
    ('RNF01', 'Segurança', 'O site usa apenas arquivos do próprio servidor e bloqueia códigos de origem '
                           'desconhecida, o que dificulta ataques pela página.'),
    ('RNF02', 'Segurança', 'Toda a navegação acontece por conexão segura (HTTPS), e o navegador recebe instruções '
                           'para proteger a sessão de quem está usando.'),
    ('RNF03', 'Segurança', 'A administradora e as profissionais precisam de uma segunda confirmação para entrar '
                           '(código de um aplicativo no celular), com códigos reserva para emergências.'),
    ('RNF04', 'Segurança', 'Os códigos enviados por SMS valem por pouco tempo, aceitam poucas tentativas e servem '
                           'para uma única finalidade.'),
    ('RNF05', 'Segurança', 'O sistema limita a quantidade de tentativas e de envios por pessoa e por aparelho, '
                           'evitando abusos e gastos desnecessários com SMS.'),
    ('RNF06', 'Privacidade', 'As páginas com dados pessoais não ficam guardadas no navegador nem aparecem em sites '
                             'de busca, e são apagadas do navegador ao sair.'),
    ('RNF07', 'Segurança', 'Senhas e chaves de acesso aos serviços ficam fora do código, apenas na configuração '
                           'do servidor.'),
    ('RNF08', 'Confiabilidade', 'Se um serviço de mensagens estiver fora do ar, nada é marcado como enviado por '
                                'engano e os e-mails ficam guardados para uma nova tentativa.'),
    ('RNF09', 'LGPD', 'Toda autorização dada pela cliente (política, saúde, termos e comunicação) fica registrada '
                      'com a data, o texto que ela viu e a origem, servindo de comprovante.'),
    ('RNF10', 'LGPD', 'Os dados que não são mais necessários são apagados ou tornados anônimos sozinhos, nos prazos '
                      'definidos; o prontuário é guardado pelo prazo exigido (20 anos).'),
    ('RNF11', 'LGPD', 'A cliente exerce os seus direitos pela própria área: ver e baixar os dados, pedir correção '
                      'ou exclusão e mudar ou retirar autorizações.'),
    ('RNF12', 'LGPD', 'As ações da equipe e as alterações de dados ficam num histórico que não pode ser apagado '
                      'nem modificado.'),
    ('RNF13', 'Integridade', 'O banco de dados impede erros como dois atendimentos no mesmo horário para a mesma '
                             'profissional ou a alteração de valores financeiros já lançados.'),
    ('RNF14', 'Confiabilidade', 'Cada atualização do sistema é aplicada por inteiro ou não é aplicada, e há cópia '
                                'de segurança para recuperar os dados se algo der errado.'),
    ('RNF15', 'Confiabilidade', 'A situação de um atendimento só muda por caminhos permitidos (um atendimento '
                                'cancelado não pode virar realizado, por exemplo) e cada mudança fica registrada.'),
    ('RNF16', 'Desempenho', 'O sistema responde com rapidez no volume da clínica (cerca de 60 atendimentos por mês) '
                            'usando um servidor pequeno e de baixo custo.'),
    ('RNF17', 'Disponibilidade', 'O sistema confere sozinho se está funcionando e registra as falhas, inclusive as '
                                 'das tarefas automáticas.'),
    ('RNF18', 'Qualidade', 'Cerca de 2.000 testes automáticos conferem o funcionamento a cada alteração, antes de '
                           'ela ir para o ar.'),
    ('RNF19', 'Usabilidade', 'As telas são em português, com linguagem de estética e bem-estar, boa leitura, uso '
                             'pelo teclado e funcionamento no celular.'),
    ('RNF20', 'Manutenção', 'O código é dividido em partes independentes, com cada regra num lugar só, o que '
                            'facilita as correções.'),
    ('RNF21', 'Implantação', 'O sistema funciona na nuvem (Railway), com um ambiente de testes separado do '
                             'ambiente usado pela clínica.'),
    ('RNF22', 'Privacidade', 'As informações de saúde ficam criptografadas: uma cópia do banco de dados, sem a '
                             'chave, não permite lê-las.'),
    ('RNF23', 'Manutenção', 'O banco de dados segue um padrão único de nomes e de regras em todas as tabelas.'),
    ('RNF24', 'Documentação', 'O sistema tem documentação, diagramas e a descrição das integrações, mantidos junto '
                              'com o código.'),
]


def _bordas(tabela):
    """Linhas finas pretas em toda a tabela, sem sombreamento."""
    tbl_pr = tabela._tbl.tblPr
    bordas = OxmlElement('w:tblBorders')
    for lado in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        b = OxmlElement(f'w:{lado}')
        b.set(qn('w:val'), 'single')
        b.set(qn('w:sz'), '4')
        b.set(qn('w:color'), '000000')
        bordas.append(b)
    tbl_pr.append(bordas)


def _numero_pagina(paragrafo):
    run = paragrafo.add_run()
    for tipo, texto in (('begin', None), (None, 'PAGE'), ('end', None)):
        if tipo:
            el = OxmlElement('w:fldChar')
            el.set(qn('w:fldCharType'), tipo)
        else:
            el = OxmlElement('w:instrText')
            el.set(qn('xml:space'), 'preserve')
            el.text = texto
        run._r.append(el)


def _tabela(doc, cabecalho, linhas, larguras):
    t = doc.add_table(rows=1, cols=len(cabecalho))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    _bordas(t)
    for i, txt in enumerate(cabecalho):
        cel = t.rows[0].cells[i]
        cel.text = ''
        cel.paragraphs[0].add_run(txt).bold = True
    for linha in linhas:
        cels = t.add_row().cells
        for i, txt in enumerate(linha):
            cels[i].text = txt
    for row in t.rows:
        for i, w in enumerate(larguras):
            row.cells[i].width = Cm(w)
            for p in row.cells[i].paragraphs:
                p.paragraph_format.space_after = Pt(2)
    return t


def documento():
    doc = Document()
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21)
    sec.top_margin = sec.left_margin = Cm(3)
    sec.bottom_margin = sec.right_margin = Cm(2)
    normal = doc.styles['Normal']
    normal.font.name = 'Times New Roman'
    normal.element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman')
    normal.font.size = Pt(12)
    normal.paragraph_format.space_after = Pt(6)
    _numero_pagina(sec.footer.paragraphs[0])
    sec.footer.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

    def titulo(txt, tam=12, centro=False, antes=12):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(antes)
        if centro:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(txt)
        r.bold = True
        r.font.size = Pt(tam)
        return p

    def texto(txt):
        p = doc.add_paragraph(txt)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.first_line_indent = Cm(1.25)
        return p

    titulo('REQUISITOS DO SISTEMA', 14, centro=True, antes=0)
    p = doc.add_paragraph('Jaqueline Aranha Estética — sistema de agendamento e gestão da clínica')
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    titulo('1 INTRODUÇÃO')
    texto('Este documento lista o que o sistema da clínica Jaqueline Aranha Estética deve fazer e de que forma '
          'deve funcionar. Os requisitos funcionais descrevem as tarefas que o sistema realiza para as clientes, '
          'para a equipe e de forma automática. Os requisitos não funcionais descrevem as qualidades esperadas, '
          'como segurança, privacidade, desempenho e facilidade de uso.')
    texto('O sistema tem três públicos: as clientes, que usam o site e a área "Meus agendamentos"; a administradora '
          'da clínica, que usa o painel de gestão; e as profissionais, que usam um espaço próprio com a sua agenda. '
          'Algumas tarefas são feitas sozinhas pelo sistema, todos os dias.')

    titulo('2 REQUISITOS FUNCIONAIS')
    _tabela(doc, ['Código', 'O sistema deve'], FUNCIONAIS, [2.0, 14.0])

    titulo('3 REQUISITOS NÃO FUNCIONAIS')
    _tabela(doc, ['Código', 'Tipo', 'Descrição'], NAO_FUNCIONAIS, [2.0, 3.0, 11.0])
    return doc


def main():
    rascunho = os.path.join(os.path.dirname(SAIDA), '~rascunho-requisitos.docx')
    documento().save(rascunho)
    try:
        import win32com.client

        word = win32com.client.DispatchEx('Word.Application')
        word.Visible = False
        word.DisplayAlerts = 0
    except Exception as e:  # noqa: BLE001
        sys.exit(f'Word indisponível ({e!r}); o rascunho ficou em {rascunho}')
    try:
        d = word.Documents.Open(rascunho, ReadOnly=False, AddToRecentFiles=False)
        try:
            d.ExportAsFixedFormat(OutputFileName=SAIDA, ExportFormat=17)   # wdExportFormatPDF
        finally:
            d.Close(False)
    finally:
        word.Quit()
    os.remove(rascunho)
    print('PDF:', SAIDA)


if __name__ == '__main__':
    main()
