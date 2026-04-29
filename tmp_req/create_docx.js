const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType,
  BorderStyle, WidthType, ShadingType,
  PageNumber, PageBreak, TabStopType, TabStopPosition
} = require("docx");

// ── Colors ──
const DARK = "2D2D2D";
const ACCENT = "2E75B6";
const ACCENT_LIGHT = "D6E4F0";
const LIGHT_BG = "F5F8FB";
const WHITE = "FFFFFF";
const HEADER_BG = "2E75B6";
const MUTED = "777777";

const thinBorder = { style: BorderStyle.SINGLE, size: 1, color: "D0D0D0" };
const borders = { top: thinBorder, bottom: thinBorder, left: thinBorder, right: thinBorder };

const PAGE_W = 11906;
const PAGE_H = 16838;
const MARGIN_V = 1440;
const MARGIN_H = 1080;
const CONTENT_W = PAGE_W - MARGIN_H * 2;
const COL_CODE = 1200;
const COL_DESC = CONTENT_W - COL_CODE;

function moduleHeader(text) {
  return new TableRow({
    children: [new TableCell({
      borders,
      width: { size: CONTENT_W, type: WidthType.DXA },
      columnSpan: 2,
      shading: { fill: HEADER_BG, type: ShadingType.CLEAR },
      margins: { top: 100, bottom: 100, left: 160, right: 160 },
      children: [new Paragraph({
        children: [new TextRun({ text, bold: true, color: WHITE, font: "Arial", size: 21 })]
      })]
    })]
  });
}

function colHeaders() {
  return new TableRow({
    children: [
      new TableCell({
        borders,
        width: { size: COL_CODE, type: WidthType.DXA },
        shading: { fill: ACCENT_LIGHT, type: ShadingType.CLEAR },
        margins: { top: 70, bottom: 70, left: 160, right: 160 },
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "C\u00f3digo", bold: true, font: "Arial", size: 19, color: DARK })]
        })]
      }),
      new TableCell({
        borders,
        width: { size: COL_DESC, type: WidthType.DXA },
        shading: { fill: ACCENT_LIGHT, type: ShadingType.CLEAR },
        margins: { top: 70, bottom: 70, left: 160, right: 160 },
        children: [new Paragraph({
          children: [new TextRun({ text: "Descri\u00e7\u00e3o", bold: true, font: "Arial", size: 19, color: DARK })]
        })]
      })
    ]
  });
}

function reqRow(code, desc, alt = false) {
  const bg = alt ? LIGHT_BG : WHITE;
  return new TableRow({
    children: [
      new TableCell({
        borders,
        width: { size: COL_CODE, type: WidthType.DXA },
        shading: { fill: bg, type: ShadingType.CLEAR },
        margins: { top: 70, bottom: 70, left: 160, right: 160 },
        verticalAlign: "center",
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: code, bold: true, font: "Arial", size: 19, color: ACCENT })]
        })]
      }),
      new TableCell({
        borders,
        width: { size: COL_DESC, type: WidthType.DXA },
        shading: { fill: bg, type: ShadingType.CLEAR },
        margins: { top: 70, bottom: 70, left: 160, right: 160 },
        children: [new Paragraph({
          spacing: { line: 288 },
          children: [new TextRun({ text: desc, font: "Arial", size: 19, color: DARK })]
        })]
      })
    ]
  });
}

function sectionTitle(text) {
  return new Paragraph({
    spacing: { before: 240, after: 160 },
    children: [new TextRun({ text, bold: true, font: "Arial", size: 30, color: DARK })]
  });
}

function separator() {
  return new Paragraph({
    spacing: { after: 240 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: ACCENT, space: 8 } },
    children: []
  });
}

// ── RF ──
const rfModules = [
  {
    name: "M\u00f3dulo: Administra\u00e7\u00e3o do Sistema",
    reqs: [
      ["RF01", "O sistema deve permitir o cadastro, edi\u00e7\u00e3o e desativa\u00e7\u00e3o de usu\u00e1rios administrativos com autentica\u00e7\u00e3o por e-mail e senha. Cada usu\u00e1rio possui um papel (role) fixo: ADMIN, RECEPCIONISTA ou PROFISSIONAL, podendo ser vinculado opcionalmente a um profissional."],
      ["RF01A", "O sistema deve permitir ao administrador cadastrar novos usu\u00e1rios via painel administrativo (/painel/usuarios/), com gera\u00e7\u00e3o autom\u00e1tica de senha inicial segura enviada por e-mail caso n\u00e3o seja informada manualmente, incluindo mensagem de boas-vindas com credenciais e link de acesso."],
      ["RF01B", "O sistema deve permitir ao administrador acionar o fluxo de redefini\u00e7\u00e3o de senha de qualquer usu\u00e1rio via painel, enviando e-mail com token de validade de 1 hora para o usu\u00e1rio definir nova senha."],
      ["RF01C", "O sistema deve permitir ao administrador ativar e desativar contas de usu\u00e1rio via painel (soft toggle), impedindo login de contas desativadas, com prote\u00e7\u00e3o contra auto-desativa\u00e7\u00e3o."],
      ["RF01D", "O sistema deve oferecer autentica\u00e7\u00e3o de dois fatores (2FA TOTP) opcional por usu\u00e1rio, com setup via QR code (Google Authenticator/Authy/Microsoft Authenticator), armazenamento via django-otp, e middleware de enforcement que exige o c\u00f3digo de 6 d\u00edgitos p\u00f3s-login para acesso ao painel administrativo enquanto o dispositivo estiver confirmado."],
      ["RF01E", "O sistema deve disponibilizar tela de branding (/painel/branding/) para o administrador editar nome, subt\u00edtulo, e-mail, telefone, endere\u00e7o, WhatsApp, Instagram, URL do site, paleta de cores (primaria/accent/dark) e fazer upload de logo (PNG/JPG/SVG/WebP at\u00e9 5MB), com persist\u00eancia em ConfiguracaoSistema e invalida\u00e7\u00e3o autom\u00e1tica de cache via signal."],
      ["RF02", "O sistema deve controlar o acesso com base no papel (role) do usu\u00e1rio: Administradores t\u00eam acesso total; Recepcionistas gerenciam agenda, clientes e aprovam agendamentos; Profissionais visualizam e aprovam apenas seus pr\u00f3prios atendimentos."],
      ["RF03", "O sistema deve disponibilizar configura\u00e7\u00f5es gerais em formato chave-valor (ex.: hor\u00e1rio de funcionamento, pol\u00edtica de cancelamento, intervalo entre procedimentos), edit\u00e1veis via painel administrativo (/painel/configuracoes/) com sugest\u00f5es de chaves recomendadas pr\u00e9-cadastradas."],
      ["RF04", "O sistema deve registrar automaticamente logs de auditoria para toda a\u00e7\u00e3o cr\u00edtica (cria\u00e7\u00e3o, edi\u00e7\u00e3o, exclus\u00e3o, aprova\u00e7\u00e3o, rejei\u00e7\u00e3o, bulk actions, gest\u00e3o de usu\u00e1rios, altera\u00e7\u00e3o de configura\u00e7\u00f5es), armazenando usu\u00e1rio respons\u00e1vel, tabela afetada, ID do registro, detalhes em JSON e data/hora."],
    ]
  },
  {
    name: "M\u00f3dulo: Gest\u00e3o de Clientes",
    reqs: [
      ["RF05", "O sistema deve permitir o cadastro e edi\u00e7\u00e3o de clientes via painel administrativo (/painel/clientes/<id>/) com dados pessoais completos: nome completo, data de nascimento, CPF, RG, profiss\u00e3o, endere\u00e7o, CEP, telefone, e-mail, flag ativo, consentimento de comunica\u00e7\u00e3o LGPD."],
      ["RF06", "O sistema deve manter um prontu\u00e1rio eletr\u00f4nico por cliente, composto por question\u00e1rio de anamnese com perguntas configur\u00e1veis pelo administrador (tipos: texto livre ou booleano)."],
      ["RF07", "O sistema deve exibir o hist\u00f3rico completo de atendimentos por cliente, incluindo procedimento, profissional, valor cobrado, status e data."],
      ["RF08", "O sistema deve controlar automaticamente as faltas consecutivas do cliente. Ao atingir 3 faltas, o cliente \u00e9 bloqueado para agendamento on-line. O contador \u00e9 resetado ao comparecer (status REALIZADO)."],
      ["RF09", "O sistema deve permitir que o cliente acesse o painel \u201cMeus Agendamentos\u201d mediante verifica\u00e7\u00e3o por c\u00f3digo OTP de 6 d\u00edgitos enviado via WhatsApp, com validade de 10 minutos e uso \u00fanico."],
    ]
  },
  {
    name: "M\u00f3dulo: Gest\u00e3o de Profissionais",
    reqs: [
      ["RF10", "O sistema deve permitir o cadastro de profissionais com nome, especialidade, telefone e status (ativo/inativo), podendo vincul\u00e1-los a um usu\u00e1rio do sistema para acesso ao painel."],
      ["RF11", "O sistema deve permitir a defini\u00e7\u00e3o da disponibilidade semanal de cada profissional (dia da semana, hor\u00e1rio de in\u00edcio e fim), com restri\u00e7\u00e3o de unicidade por profissional/dia."],
      ["RF12", "O sistema deve permitir o bloqueio de per\u00edodos na agenda de um profissional (f\u00e9rias, feriados, afastamento), com registro de motivo e per\u00edodo exato."],
      ["RF13", "O sistema deve permitir a associa\u00e7\u00e3o de procedimentos espec\u00edficos a cada profissional, restringindo quais servi\u00e7os cada um pode executar."],
    ]
  },
  {
    name: "M\u00f3dulo: Procedimentos e Tabela de Pre\u00e7os",
    reqs: [
      ["RF14", "O sistema deve permitir o cadastro de procedimentos com nome, descri\u00e7\u00e3o, dura\u00e7\u00e3o em minutos e status (ativo/inativo)."],
      ["RF15", "O sistema deve suportar tabela de pre\u00e7os com hierarquia: pre\u00e7o espec\u00edfico por profissional sobrep\u00f5e o pre\u00e7o-base gen\u00e9rico do procedimento."],
      ["RF16", "O sistema deve separar o cat\u00e1logo de servi\u00e7os em categorias (faciais e corporais) para exibi\u00e7\u00e3o organizada no site p\u00fablico."],
    ]
  },
  {
    name: "M\u00f3dulo: Agendamento On-line",
    reqs: [
      ["RF17", "O sistema deve oferecer agendamento on-line p\u00fablico em fluxo de 3 etapas: (1) sele\u00e7\u00e3o de procedimento, (2) escolha de data/hor\u00e1rio/profissional, (3) confirma\u00e7\u00e3o com dados do cliente."],
      ["RF18", "O sistema deve calcular hor\u00e1rios dispon\u00edveis em slots de 30 minutos, respeitando disponibilidade semanal, bloqueios de agenda e conflitos com atendimentos existentes."],
      ["RF19", "O sistema deve fornecer API que retorne os dias dispon\u00edveis no m\u00eas para um procedimento, considerando apenas profissionais ativos que o realizam."],
      ["RF20", "Caso o telefone informado no agendamento n\u00e3o exista na base, o sistema deve criar automaticamente um novo registro de cliente."],
      ["RF21", "O sistema deve aplicar automaticamente promo\u00e7\u00f5es vigentes no momento do agendamento, calculando desconto percentual ou pre\u00e7o promocional."],
      ["RF22", "Ap\u00f3s confirma\u00e7\u00e3o, o sistema deve exibir p\u00e1gina de sucesso com resumo completo e bot\u00e3o para compartilhamento via WhatsApp."],
      ["RF23", "O sistema deve permitir reagendamento de atendimentos, mantendo v\u00ednculo com o atendimento original para rastreabilidade."],
    ]
  },
  {
    name: "M\u00f3dulo: Fluxo de Aprova\u00e7\u00e3o de Agendamento",
    reqs: [
      ["RF24", "O sistema deve criar todo agendamento on-line com status inicial PENDENTE, exigindo aprova\u00e7\u00e3o antes de virar AGENDADO."],
      ["RF25", "O sistema deve enviar e-mail autom\u00e1tico ao profissional respons\u00e1vel contendo resumo do agendamento e dois links com token: aprovar (\u2192 AGENDADO) e rejeitar (\u2192 CANCELADO)."],
      ["RF26", "O profissional logado deve visualizar a fila de agendamentos PENDENTES em seu painel (/profissional/) com bot\u00f5es inline Aprovar e Rejeitar."],
      ["RF27", "O painel gerencial (overview) deve exibir card destacado \u201cPendentes de aprova\u00e7\u00e3o\u201d com contador, lista dos 15 mais pr\u00f3ximos e a\u00e7\u00f5es inline."],
      ["RF28", "A tela /painel/agendamentos/ deve permitir filtrar por status=PENDENTE e apresentar bot\u00f5es Aprovar/Rejeitar inline em cada card pendente, destacados visualmente."],
      ["RF28A", "A tela /painel/agendamentos/ deve oferecer a\u00e7\u00f5es em massa (bulk actions) sobre agendamentos pendentes, permitindo selecionar m\u00faltiplos via checkbox (+ select-all) e aprovar ou rejeitar todos simultaneamente em um \u00fanico POST, com barra flutuante mostrando contador e bot\u00f5es de a\u00e7\u00e3o/limpar, registrando log de auditoria por registro processado."],
      ["RF29", "Ao aprovar, o sistema deve mudar status para AGENDADO, registrar log de auditoria (tanto para aprova\u00e7\u00e3o via painel admin quanto via painel do profissional) e disparar e-mail de confirma\u00e7\u00e3o ao cliente."],
      ["RF30", "Ao rejeitar, o sistema deve mudar status para CANCELADO, registrar log de auditoria (tanto para rejei\u00e7\u00e3o via painel admin quanto via painel do profissional) e disparar e-mail de cancelamento ao cliente."],
    ]
  },
  {
    name: "M\u00f3dulo: Gest\u00e3o de Atendimentos",
    reqs: [
      ["RF31", "O sistema deve registrar cada atendimento com: data/hora de in\u00edcio e fim, profissional, procedimento, cliente, valor cobrado, valor original, status e observa\u00e7\u00f5es."],
      ["RF32", "O sistema deve gerenciar o ciclo de vida do status: PENDENTE \u2192 AGENDADO \u2192 CONFIRMADO \u2192 REALIZADO | CANCELADO | FALTOU."],
      ["RF33", "Ao marcar FALTOU, o sistema deve incrementar as faltas consecutivas do cliente e, ao atingir 3, bloquear o agendamento on-line automaticamente."],
      ["RF34", "Ao marcar REALIZADO, o sistema deve resetar o contador de faltas do cliente para zero."],
      ["RF35", "Ao marcar CANCELADO ou FALTOU, o sistema deve acionar a notifica\u00e7\u00e3o da fila de espera para o mesmo procedimento e data."],
      ["RF36", "O sistema deve permitir a gera\u00e7\u00e3o de termo de consentimento digital vinculado ao atendimento, registrando IP, data/hora e usu\u00e1rio respons\u00e1vel."],
      ["RF37", "O sistema deve marcar automaticamente como FALTOU atendimentos sem atualiza\u00e7\u00e3o de status 24 horas ap\u00f3s o hor\u00e1rio agendado."],
    ]
  },
  {
    name: "M\u00f3dulo: Notifica\u00e7\u00f5es Autom\u00e1ticas via WhatsApp",
    reqs: [
      ["RF38", "O sistema deve enviar lembrete via WhatsApp no dia anterior (D\u20131, \u00e0s 8h) para agendamentos com status AGENDADO, com link de confirma\u00e7\u00e3o/cancelamento."],
      ["RF39", "O sistema deve enviar segundo lembrete 2 horas antes do atendimento, caso o cliente ainda n\u00e3o tenha respondido."],
      ["RF40", "O sistema deve permitir que o cliente confirme ou cancele presen\u00e7a via link com token \u00fanico de 64 caracteres, sem necessidade de login."],
      ["RF41", "O sistema deve registrar cada notifica\u00e7\u00e3o com: tipo (LEMBRETE, CONFIRMA\u00c7\u00c3O, CANCELAMENTO, NPS, TERMO), canal, status de envio, resposta do cliente e timestamps."],
      ["RF42", "O sistema deve enviar c\u00f3digo OTP via WhatsApp para acesso ao painel \u201cMeus Agendamentos\u201d."],
      ["RF43", "O sistema deve notificar clientes da fila de espera quando surgir vaga por cancelamento ou falta."],
      ["RF44", "O sistema deve enviar pesquisa de satisfa\u00e7\u00e3o NPS via WhatsApp 24 horas ap\u00f3s atendimento REALIZADO."],
      ["RF45", "O sistema deve enviar alerta ao administrador quando uma avalia\u00e7\u00e3o NPS receber nota 1 ou 2 (detratores)."],
    ]
  },
  {
    name: "M\u00f3dulo: Painel do Cliente",
    reqs: [
      ["RF46", "O sistema deve permitir que o cliente consulte seus agendamentos futuros no site, ap\u00f3s verifica\u00e7\u00e3o via telefone + c\u00f3digo OTP."],
      ["RF47", "O sistema deve permitir que o cliente cancele um agendamento futuro diretamente pelo painel."],
    ]
  },
  {
    name: "M\u00f3dulo: Promo\u00e7\u00f5es",
    reqs: [
      ["RF48", "O sistema deve permitir cadastro, edi\u00e7\u00e3o e exclus\u00e3o de promo\u00e7\u00f5es com: nome, descri\u00e7\u00e3o, procedimento vinculado, desconto percentual ou pre\u00e7o promocional, per\u00edodo de vig\u00eancia, imagem e status."],
      ["RF49", "O sistema deve exibir no site p\u00fablico apenas promo\u00e7\u00f5es ativas dentro do per\u00edodo de vig\u00eancia."],
    ]
  },
  {
    name: "M\u00f3dulo: Pacotes de Sess\u00f5es",
    reqs: [
      ["RF50", "O sistema deve permitir o cadastro de pacotes compostos por m\u00faltiplos procedimentos, cada qual com quantidade de sess\u00f5es, nome, descri\u00e7\u00e3o, pre\u00e7o total e validade em meses."],
      ["RF51", "O sistema deve registrar a venda de pacotes a clientes com data de compra, valor pago, data de expira\u00e7\u00e3o (calculada automaticamente) e status (ATIVO, FINALIZADO, CANCELADO, EXPIRADO)."],
      ["RF52", "O sistema deve debitar automaticamente uma sess\u00e3o do pacote quando o atendimento correspondente for marcado como REALIZADO."],
      ["RF53", "O sistema deve finalizar automaticamente o pacote quando todas as sess\u00f5es forem consumidas."],
      ["RF54", "O sistema deve enviar alertas de expira\u00e7\u00e3o de pacotes com 7 dias e 1 dia de anteced\u00eancia, e expirar automaticamente pacotes vencidos."],
    ]
  },
  {
    name: "M\u00f3dulo: Fila de Espera",
    reqs: [
      ["RF55", "O sistema deve permitir a inscri\u00e7\u00e3o na fila de espera com: procedimento desejado, data preferencial, turno (manh\u00e3/tarde/noite) e profissional desejado (opcional)."],
      ["RF56", "O sistema deve notificar automaticamente os clientes da fila quando surgir vaga, com link para agendamento e token de reserva."],
    ]
  },
  {
    name: "M\u00f3dulo: Pesquisa de Satisfa\u00e7\u00e3o (NPS)",
    reqs: [
      ["RF57", "O sistema deve enviar pesquisa de satisfa\u00e7\u00e3o via WhatsApp 24 horas ap\u00f3s a conclus\u00e3o do atendimento, evitando envio duplicado."],
      ["RF58", "O sistema deve registrar a avalia\u00e7\u00e3o NPS com nota de 1 a 5, coment\u00e1rio opcional e data, vinculada ao atendimento (rela\u00e7\u00e3o 1:1)."],
      ["RF59", "O sistema deve alertar o administrador automaticamente quando uma avalia\u00e7\u00e3o receber nota 1 ou 2 (detratores)."],
    ]
  },
  {
    name: "M\u00f3dulo: Dashboard e Relat\u00f3rios",
    reqs: [
      ["RF60", "O sistema deve exibir dashboard com indicadores: atendimentos do dia/semana, clientes ativos, faturamento mensal, gr\u00e1fico de agendamentos dos \u00faltimos 7 dias e card de pendentes de aprova\u00e7\u00e3o."],
      ["RF61", "O sistema deve exibir painel de notifica\u00e7\u00f5es com estat\u00edsticas consolidadas e filtros por tipo e status."],
      ["RF62", "O sistema deve permitir a exporta\u00e7\u00e3o de relat\u00f3rio de atendimentos dos \u00faltimos 30 dias em formato Excel (.xlsx)."],
      ["RF63", "O sistema deve exibir consulta ao log de auditoria com filtros por tabela, a\u00e7\u00e3o, per\u00edodo e pagina\u00e7\u00e3o."],
      ["RF63A", "O sistema deve exibir vis\u00e3o de calend\u00e1rio (FullCalendar 6.x) em /painel/calendario/ com modos dia/semana/m\u00eas, filtro por profissional, cores por status, modal de detalhes do atendimento ao clicar, e reagendamento via drag-drop com confirma\u00e7\u00e3o e log de auditoria (movimento bloqueado para status REALIZADO/CANCELADO/FALTOU)."],
      ["RF63B", "O sistema deve oferecer dashboard de compliance de termos (/painel/termos/compliance/) com: lista de versoes ativas + contador de assinados/pendentes, progresso percentual, e visualiza\u00e7\u00e3o detalhada de clientes pendentes por termo com a\u00e7\u00f5es r\u00e1pidas de contato (WhatsApp/e-mail)."],
    ]
  },
  {
    name: "M\u00f3dulo: Estoque",
    reqs: [
      ["RF64", "O sistema deve permitir o cadastro de categorias de produtos (ex.: injet\u00e1veis, cosm\u00e9ticos, descart\u00e1veis) com nome e descri\u00e7\u00e3o."],
      ["RF65", "O sistema deve permitir o cadastro de produtos com: nome, descri\u00e7\u00e3o, marca, unidade de medida (UN, ML, G, AMP, FR, TB, CX), quantidade em estoque, estoque m\u00ednimo, pre\u00e7o de custo, pre\u00e7o de venda (opcional), lote e data de validade."],
      ["RF66", "O sistema deve registrar movimenta\u00e7\u00f5es de estoque com tipo (ENTRADA, SA\u00cdDA_USO, SA\u00cdDA_VENDA, AJUSTE, DESCARTE), quantidade, v\u00ednculo opcional ao atendimento e usu\u00e1rio respons\u00e1vel."],
      ["RF67", "O sistema deve alertar automaticamente quando a quantidade em estoque de um produto atingir ou ficar abaixo do estoque m\u00ednimo definido."],
      ["RF68", "O sistema deve identificar e alertar sobre produtos com data de validade vencida ou pr\u00f3xima do vencimento."],
    ]
  },
  {
    name: "M\u00f3dulo: Site P\u00fablico Institucional",
    reqs: [
      ["RF69", "O sistema deve disponibilizar p\u00e1ginas p\u00fablicas: p\u00e1gina inicial, quem somos, termos de uso, pol\u00edtica de privacidade, FAQ, depoimentos e galeria."],
      ["RF70", "O sistema deve exibir cat\u00e1logo p\u00fablico de servi\u00e7os por categorias, com nomes, descri\u00e7\u00f5es e pre\u00e7os."],
      ["RF71", "O sistema deve disponibilizar formul\u00e1rio de contato para envio de mensagens \u00e0 cl\u00ednica."],
    ]
  },
  {
    name: "M\u00f3dulo: Regras de Agenda Avan\u00e7adas (Sprint 1)",
    reqs: [
      ["RF72", "O sistema deve permitir definir buffer (em minutos, 0-120) por procedimento, bloqueando slots adjacentes ap\u00f3s atendimento para evitar overbook."],
      ["RF73", "O sistema deve permitir configurar min_notice_horas (anteced\u00eancia m\u00ednima) e max_advance_dias (janela m\u00e1xima futura) por profissional."],
      ["RF74", "O sistema deve permitir cadastrar exce\u00e7\u00f5es de disponibilidade por data (FOLGA bloqueia o dia; HORARIO_DIFERENTE substitui regra semanal)."],
      ["RF75", "O painel deve oferecer visualiza\u00e7\u00e3o de calend\u00e1rio (FullCalendar) com views m\u00eas/semana/dia, drag-drop para reagendar, eventos de bloqueio e folga em background."],
      ["RF76", "O dashboard overview deve exibir KPIs: ticket m\u00e9dio, taxa de ocupa\u00e7\u00e3o semanal, clientes ativos 90 dias e NPS m\u00e9dio 30 dias."],
    ]
  },
  {
    name: "M\u00f3dulo: UX Painel + PWA (Sprint 2)",
    reqs: [
      ["RF77", "O painel deve oferecer filtro instant\u00e2neo client-side em listas de agendamentos, pacientes e usu\u00e1rios, com normaliza\u00e7\u00e3o de acentos e debounce."],
      ["RF78", "A ficha do paciente deve exibir KPIs: LTV, ticket m\u00e9dio, qtd realizados, \u00faltima visita, procedimento mais frequente e contadores de no-show/cancelamento."],
      ["RF79", "A ficha do paciente deve apresentar timeline visual cronol\u00f3gica dos atendimentos com cores por status."],
      ["RF80", "O sistema deve operar como PWA com manifest, service worker (cache-first imagens c/ TTL, stale-while-revalidate est\u00e1ticos, network-first HTML c/ fallback offline) e instalable em home screen."],
    ]
  },
  {
    name: "M\u00f3dulo: UX P\u00fablico + Integra\u00e7\u00f5es Externas (Sprint 3)",
    reqs: [
      ["RF81", "Cada profissional ativo deve possuir slug \u00fanico e URL p\u00fablica /agendar/<slug>/ que redireciona ao booking com profissional pr\u00e9-selecionado."],
      ["RF82", "Cada profissional deve possuir feed iCalendar (text/calendar) protegido por token em /agenda/<slug>/feed.ics?token=<>, exportando atendimentos AGENDADO/CONFIRMADO/REALIZADO no range -30d..+90d."],
      ["RF83", "O booking p\u00fablico deve permitir filtrar procedimentos por categoria (Facial, Corporal, Capilar, Outro) via tabs."],
      ["RF84", "O sistema deve permitir reagendamento self-service via link m\u00e1gico /reagendar/<token>/ com janela m\u00ednima de 24h, transi\u00e7\u00e3o at\u00f4mica do antigo para REAGENDADO."],
    ]
  },
  {
    name: "M\u00f3dulo: Workflow + Push + FSM (Sprint 4)",
    reqs: [
      ["RF85", "O sistema deve oferecer engine de workflows configur\u00e1veis por regra (trigger ON_BOOK/BEFORE_EVENT/AFTER_EVENT/ON_CANCEL/ON_NO_SHOW + offset + a\u00e7\u00e3o SEND_EMAIL/SMS/WHATSAPP/PUSH/WEBHOOK + template + config_json)."],
      ["RF86", "O sistema deve garantir deduplica\u00e7\u00e3o de execu\u00e7\u00f5es de workflow via constraint UNIQUE(regra, atendimento) e registrar status (OK/FALHOU/SKIPPED) em WorkflowExecucao."],
      ["RF87", "O sistema deve suportar Web Push (VAPID/pywebpush) com subscription por usu\u00e1rio admin/profissional, endpoints /webpush/{public-key,subscribe,unsubscribe} e disparo autom\u00e1tico ao profissional em novo agendamento."],
      ["RF88", "Atendimento deve expor m\u00e9todos de transi\u00e7\u00e3o FSM (confirmar/cancelar/marcar_realizado/marcar_falta/aprovar) com valida\u00e7\u00e3o de origem e auditoria autom\u00e1tica em LogAuditoria."],
    ]
  },
  {
    name: "M\u00f3dulo: Recorr\u00eancia + Anamnese + GCal + Embed (Sprint 5)",
    reqs: [
      ["RF89", "BloqueioAgenda deve aceitar regra iCal RRULE para recorr\u00eancia (ex: FREQ=WEEKLY;BYDAY=WE) com data limite opcional, expandindo ocorr\u00eancias na janela do dia consultado."],
      ["RF90", "O sistema deve oferecer formul\u00e1rios de anamnese configur\u00e1veis (escopo GLOBAL/CATEGORIA/PROCEDIMENTO) com schema JSON din\u00e2mico (bool/text/longtext/select/number/date), renderizados no booking p\u00fablico antes da confirma\u00e7\u00e3o."],
      ["RF91", "O sistema deve oferecer integra\u00e7\u00e3o OAuth bidirecional com Google Calendar por profissional (refresh_token, push outbound em novo agendamento, pull eventos externos como BloqueioAgenda)."],
      ["RF92", "O sistema deve disponibilizar widget standalone /embed/agendar/ (X-Frame-Options exempt) para inser\u00e7\u00e3o em iframe no Linktree, Instagram bio ou sites externos."],
      ["RF93", "O service worker deve aplicar cache stale-while-revalidate a APIs p\u00fablicas (/api/dias-disponiveis, /api/horarios-disponiveis, /api/buscar-procedimentos) com pre-cache de shell e limite de entradas."],
    ]
  },
];

// ── RNF ──
const rnfModules = [
  {
    name: "M\u00f3dulo: Usabilidade",
    reqs: [
      ["RNF01", "A interface web deve ser responsiva e acess\u00edvel em desktop e dispositivos m\u00f3veis, utilizando Bootstrap 5.3 e Bootstrap Icons."],
      ["RNF02", "O agendamento on-line deve seguir fluxo guiado de 3 etapas com barra de progresso visual, sem exigir cadastro pr\u00e9vio ou login."],
      ["RNF03", "O sistema deve utilizar anima\u00e7\u00f5es suaves e indicadores visuais de carregamento para melhorar a experi\u00eancia do usu\u00e1rio."],
      ["RNF04", "A\u00e7\u00f5es de aprovar/rejeitar devem exigir confirma\u00e7\u00e3o expl\u00edcita do usu\u00e1rio antes de persistir (dialog confirm)."],
    ]
  },
  {
    name: "M\u00f3dulo: Seguran\u00e7a",
    reqs: [
      ["RNF05", "O acesso ao painel administrativo deve exigir autentica\u00e7\u00e3o com e-mail e senha, com rate limiting de 5 tentativas/minuto por IP."],
      ["RNF06", "Toda a\u00e7\u00e3o cr\u00edtica deve ser registrada em log de auditoria com usu\u00e1rio, a\u00e7\u00e3o, tabela afetada e timestamp."],
      ["RNF07", "A sess\u00e3o deve ser regenerada (cycle_key) ap\u00f3s login para preven\u00e7\u00e3o de fixa\u00e7\u00e3o de sess\u00e3o. Timeout de 1 hora com refresh autom\u00e1tico."],
      ["RNF08", "Dados sens\u00edveis (PII) devem ter logging mascarado (\u00faltimos 4 caracteres)."],
      ["RNF09", "O sistema deve validar URLs de redirecionamento ap\u00f3s login para preven\u00e7\u00e3o de open redirect."],
      ["RNF10", "Backups autom\u00e1ticos di\u00e1rios do banco de dados devem ser realizados."],
      ["RNF11", "Tokens de confirma\u00e7\u00e3o devem ser strings aleat\u00f3rias de 64 caracteres, \u00fanicos e n\u00e3o previs\u00edveis."],
      ["RNF12", "C\u00f3digos OTP devem ter validade de 10 minutos, uso \u00fanico, invalidando c\u00f3digos anteriores do mesmo telefone."],
      ["RNF13", "Endpoints de aprova\u00e7\u00e3o (aprovar/rejeitar) devem exigir m\u00e9todo POST + CSRF token + verifica\u00e7\u00e3o de papel + rate limiting de 60/minuto por usu\u00e1rio, com rate limit mais restritivo de 10/minuto para bulk actions."],
      ["RNF13A", "O middleware de enforcement 2FA deve interceptar requests a /painel/ e /profissional/ exigindo verifica\u00e7\u00e3o TOTP quando o usu\u00e1rio possuir TOTPDevice confirmado, exceto para rotas isentas (login, logout, 2FA challenge/verify/setup, password reset)."],
      ["RNF13B", "Upload de logo deve validar extens\u00e3o (PNG/JPG/SVG/WebP), tamanho m\u00e1ximo de 5MB e usar default_storage para compatibilidade com S3/R2 futuro."],
    ]
  },
  {
    name: "M\u00f3dulo: Desempenho",
    reqs: [
      ["RNF14", "O sistema deve suportar no m\u00ednimo 25 usu\u00e1rios simult\u00e2neos sem degrada\u00e7\u00e3o percept\u00edvel."],
      ["RNF15", "Consultas frequentes devem utilizar \u00edndices compostos (ex.: profissional + data + status em atendimentos, procedimento + profissional em pre\u00e7os)."],
      ["RNF16", "APIs de consulta devem ter rate limiting de 30 requisi\u00e7\u00f5es/minuto para preven\u00e7\u00e3o de abuso."],
    ]
  },
  {
    name: "M\u00f3dulo: Integra\u00e7\u00e3o",
    reqs: [
      ["RNF17", "O sistema deve integrar-se com a API do WhatsApp Business (Meta) para envio de notifica\u00e7\u00f5es autom\u00e1ticas: lembretes, confirma\u00e7\u00f5es, OTP, fila de espera e pesquisas NPS."],
      ["RNF18", "Processamento ass\u00edncrono (lembretes, notifica\u00e7\u00f5es, pesquisas NPS, verifica\u00e7\u00e3o de pacotes) deve utilizar Celery com Redis como broker, com fallback s\u00edncrono em caso de indisponibilidade."],
      ["RNF19", "Jobs agendados devem ser disparados por cron externo (cron-job.org) via endpoint /cron/run/<job_name>/ autenticado por token."],
      ["RNF20", "O sistema deve expor API REST para consumo pelo aplicativo mobile (futuro), compartilhando o mesmo banco de dados."],
    ]
  },
  {
    name: "M\u00f3dulo: Conformidade",
    reqs: [
      ["RNF21", "O sistema deve gerar termos de consentimento para procedimentos est\u00e9ticos, registrando IP, data/hora e usu\u00e1rio."],
      ["RNF22", "Termos de Uso e Pol\u00edtica de Privacidade devem estar acess\u00edveis publicamente, em conformidade com a LGPD."],
    ]
  },
  {
    name: "M\u00f3dulo: Manutenibilidade",
    reqs: [
      ["RNF23", "A aplica\u00e7\u00e3o deve ser desenvolvida em Python 3.x com Django 5.2, arquitetura modular (views por dom\u00ednio: booking, admin, notifica\u00e7\u00f5es, dashboard, services)."],
      ["RNF24", "L\u00f3gica de neg\u00f3cio reativa (debitar pacote, bloquear cliente, notificar fila) deve ser implementada via Django Signals para desacoplamento."],
      ["RNF25", "Exclus\u00f5es l\u00f3gicas (soft delete via campo ativo) devem ser utilizadas para entidades cr\u00edticas: clientes, profissionais e procedimentos."],
      ["RNF26", "Campos de status devem utilizar TextChoices/IntegerChoices do Django para type-safety e elimina\u00e7\u00e3o de magic strings."],
    ]
  },
  {
    name: "M\u00f3dulo: Infraestrutura",
    reqs: [
      ["RNF27", "O sistema deve ser hospedado na plataforma Railway com deploy autom\u00e1tico, healthcheck em /healthz/ e release hook executando migrations."],
      ["RNF28", "Banco de dados PostgreSQL com suporte a JSONB para detalhes em logs de auditoria."],
      ["RNF29", "Arquivos est\u00e1ticos devem ser servidos via WhiteNoise com versionamento de cache."],
      ["RNF30", "O reposit\u00f3rio deve seguir estrutura monorepo: back-end Django + aplicativo mobile (futuro), compartilhando o mesmo banco de dados."],
    ]
  },
  {
    name: "M\u00f3dulo: Integra\u00e7\u00f5es Externas + Performance Avan\u00e7ada",
    reqs: [
      ["RNF31", "O service worker deve segmentar caches por tipo (STATIC, RUNTIME, IMAGE, API) com vers\u00e3o (v4), trim por limite de entradas e expira\u00e7\u00e3o ativa em mudan\u00e7a de vers\u00e3o."],
      ["RNF32", "Web Push deve usar VAPID via pywebpush, com fallback gracioso quando lib ou chaves estiverem ausentes (sem quebrar boot da aplica\u00e7\u00e3o)."],
      ["RNF33", "Integra\u00e7\u00e3o Google Calendar deve carregar libs google-auth via lazy import; aus\u00eancia das libs ou env vars n\u00e3o deve quebrar funcionalidades n\u00e3o-relacionadas."],
      ["RNF34", "Workflow engine deve operar via dois caminhos: signals s\u00edncronos (ON_BOOK/ON_CANCEL/ON_NO_SHOW) e endpoint cron HTTP idempotente (/cron/workflow_pendentes para BEFORE/AFTER_EVENT)."],
      ["RNF35", "Embed widget /embed/agendar/ deve ser servido com X-Frame-Options exempt e CSS standalone (sem chrome do painel admin) para uso em iframes externos."],
      ["RNF36", "Slot generator deve aplicar em ordem: (1) feriado bloqueador, (2) max_advance, (3) exce\u00e7\u00e3o de disponibilidade, (4) regra semanal, (5) min_notice, (6) atendimentos com buffer, (7) bloqueios pontuais, (8) bloqueios recorrentes via dateutil.rrule."],
    ]
  },
];

function buildModuleRows(modules) {
  const rows = [];
  for (const mod of modules) {
    rows.push(moduleHeader(mod.name));
    rows.push(colHeaders());
    mod.reqs.forEach((r, i) => rows.push(reqRow(r[0], r[1], i % 2 === 1)));
  }
  return rows;
}

const totalRF = rfModules.reduce((s, m) => s + m.reqs.length, 0);
const totalRNF = rnfModules.reduce((s, m) => s + m.reqs.length, 0);

const doc = new Document({
  styles: {
    default: {
      document: { run: { font: "Arial", size: 22, color: DARK } }
    }
  },
  sections: [
    {
      properties: {
        page: { size: { width: PAGE_W, height: PAGE_H }, margin: { top: 2880, right: 1440, bottom: 1440, left: 1440 } }
      },
      children: [
        new Paragraph({ spacing: { after: 800 }, children: [] }),
        new Paragraph({
          spacing: { after: 120 },
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "SHIVAZEN", bold: true, font: "Arial", size: 56, color: DARK, characterSpacing: 400 })]
        }),
        new Paragraph({
          spacing: { after: 60 },
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Plataforma multi-cl\u00ednica para Gest\u00e3o de Est\u00e9tica e Bem-Estar", font: "Arial", size: 26, color: ACCENT })]
        }),
        new Paragraph({
          spacing: { after: 40 },
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Instala\u00e7\u00e3o piloto: Jaqueline Aranha Est\u00e9tica", font: "Arial", size: 20, color: MUTED, italics: true })]
        }),
        new Paragraph({
          spacing: { after: 400 },
          alignment: AlignmentType.CENTER,
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: ACCENT, space: 12 } },
          children: []
        }),
        new Paragraph({ spacing: { after: 300 }, children: [] }),
        new Paragraph({
          spacing: { after: 80 },
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Documento de Especifica\u00e7\u00e3o", bold: true, font: "Arial", size: 34, color: DARK })]
        }),
        new Paragraph({
          spacing: { after: 60 },
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Requisitos Funcionais e N\u00e3o Funcionais", bold: true, font: "Arial", size: 28, color: ACCENT })]
        }),
        new Paragraph({ spacing: { after: 200 }, children: [] }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { after: 40 },
          children: [new TextRun({ text: `${totalRF} Requisitos Funcionais  \u00b7  ${totalRNF} Requisitos N\u00e3o Funcionais`, font: "Arial", size: 20, color: MUTED })]
        }),
        new Paragraph({ spacing: { after: 600 }, children: [] }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { after: 40 },
          children: [new TextRun({ text: "Vers\u00e3o 2.4  |  Abril 2026", font: "Arial", size: 20, color: MUTED })]
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Projeto Integrador \u2014 FAM", font: "Arial", size: 20, color: MUTED })]
        }),
        new Paragraph({ children: [new PageBreak()] }),
      ]
    },
    {
      properties: {
        page: { size: { width: PAGE_W, height: PAGE_H }, margin: { top: MARGIN_V, right: MARGIN_H, bottom: MARGIN_V, left: MARGIN_H } }
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: ACCENT_LIGHT, space: 4 } },
            tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
            children: [
              new TextRun({ text: "Jaqueline Aranha Est\u00e9tica", bold: true, font: "Arial", size: 16, color: ACCENT }),
              new TextRun({ text: "\tRequisitos Funcionais e N\u00e3o Funcionais", font: "Arial", size: 16, color: MUTED }),
            ]
          })]
        })
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            border: { top: { style: BorderStyle.SINGLE, size: 1, color: ACCENT_LIGHT, space: 4 } },
            children: [
              new TextRun({ text: "P\u00e1gina ", font: "Arial", size: 16, color: MUTED }),
              new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 16, color: MUTED })
            ]
          })]
        })
      },
      children: [
        sectionTitle("Requisitos Funcionais (RF)"),
        separator(),
        new Table({
          width: { size: CONTENT_W, type: WidthType.DXA },
          columnWidths: [COL_CODE, COL_DESC],
          rows: buildModuleRows(rfModules)
        }),
        new Paragraph({ children: [new PageBreak()] }),
        sectionTitle("Requisitos N\u00e3o Funcionais (RNF)"),
        separator(),
        new Table({
          width: { size: CONTENT_W, type: WidthType.DXA },
          columnWidths: [COL_CODE, COL_DESC],
          rows: buildModuleRows(rnfModules)
        }),
      ]
    }
  ]
});

const outputPath = "C:/Users/rafae/OneDrive/Projeto FAM - Shivazen/Diagramas/Requisitos Funcionais e Nao Funcionais.docx";

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync(outputPath, buffer);
  console.log("DOCX criado: " + outputPath);
  console.log(`Total: ${totalRF} RF + ${totalRNF} RNF = ${totalRF + totalRNF} requisitos`);
});
