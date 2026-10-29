// painel-calendario.js — FullCalendar do painel (painel/calendar.html).
//
// A biblioteca vem do npm, empacotada pelo Vite (nada de CDN: sem host externo na CSP e o arquivo
// sai com hash no manifest). O FullCalendar 6 injeta o proprio CSS: o template traz um
// <style data-fullcalendar nonce> vazio que ele reaproveita (CSP com nonce, sem 'unsafe-inline').
// Daqui saem: eventos por AJAX (data-url-eventos), arrasto para reagendar (data-url-mover, resposta
// no contrato de utils/respostas) e o modal do atendimento. Sem handler inline.
import { Calendar } from '@fullcalendar/core'
import ptBrLocale from '@fullcalendar/core/locales/pt-br'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import listPlugin from '@fullcalendar/list'
import interactionPlugin from '@fullcalendar/interaction'
import { postJson } from './lib/requisicao.js'

const calendarEl = document.getElementById('calendar')
const ROTULOS_STATUS = {
  PENDENTE: 'Pendente', AGENDADO: 'Agendado', CONFIRMADO: 'Confirmado', REALIZADO: 'Realizado',
  CANCELADO: 'Cancelado', FALTOU: 'Faltou', REAGENDADO: 'Reagendado',
}

// Acessibilidade do que o FullCalendar desenha (axe: role-img-alt, aria-required-children,
// scrollable-region-focusable, aria-prohibited-attr). Reaplicado a cada troca de periodo/visao.
const NOMES_SETAS = { 'fc-prev-button': 'Período anterior', 'fc-next-button': 'Próximo período' }
function ajustarAcessibilidade() {
  Object.keys(NOMES_SETAS).forEach((cls) => {
    calendarEl.querySelectorAll('.' + cls).forEach((b) => b.setAttribute('aria-label', NOMES_SETAS[cls]))
  })
  calendarEl.querySelectorAll('.fc-icon').forEach((i) => i.setAttribute('aria-hidden', 'true'))
  // A grade e um <table role="grid"> de LAYOUT (cabecalho e corpo sao outras tabelas dentro de
  // celulas role=presentation): o grid fica sem linhas (aria-required-children). Como tabela de
  // layout ela vira presentation; a informacao (dia, hora, evento) segue nas tabelas internas e
  // nos links dos eventos, que tem nome.
  calendarEl.querySelectorAll('table.fc-scrollgrid[role="grid"]').forEach((t) => {
    t.setAttribute('role', 'presentation')
    t.querySelectorAll(':scope > thead[role="rowgroup"], :scope > tbody[role="rowgroup"]')
      .forEach((g) => g.setAttribute('role', 'presentation'))
  })
  // area com rolagem precisa receber foco p/ rolar pelo teclado (WCAG 2.1.1); nome so com papel
  calendarEl.querySelectorAll('.fc-scroller').forEach((r) => {
    const rola = r.scrollHeight > r.clientHeight + 1
    if (rola && !r.hasAttribute('tabindex')) {
      r.setAttribute('tabindex', '0')
      r.setAttribute('role', 'region')
      r.setAttribute('aria-label', 'Grade da agenda (role com as setas)')
    } else if (!rola && r.getAttribute('role') === 'region') {
      r.removeAttribute('tabindex')
      r.removeAttribute('role')
      r.removeAttribute('aria-label')
    }
  })
}

function iniciar() {
  const filtroProfissional = document.getElementById('filtroProfissional')
  const modal = document.getElementById('calModal')
  const celular = window.matchMedia('(max-width: 640px)').matches
  const urlEventos = calendarEl.dataset.urlEventos
  const urlMover = calendarEl.dataset.urlMover

  function eventsSource(info, success, failure) {
    let url = urlEventos + '?start=' + encodeURIComponent(info.startStr) + '&end=' + encodeURIComponent(info.endStr)
    if (filtroProfissional.value) url += '&profissional=' + encodeURIComponent(filtroProfissional.value)
    fetch(url, { credentials: 'same-origin' })
      .then((r) => r.json())
      .then(success)
      .catch(failure)
  }

  function texto(id, valor) { document.getElementById(id).textContent = valor }

  function showEvent(info) {
    const e = info.event
    const p = e.extendedProps || {}
    if (!p.status) return   // bloqueio/folga (evento de fundo): nao ha atendimento p/ mostrar
    info.jsEvent?.preventDefault()
    texto('cmClienteNome', p.cliente_nome || e.title)
    const statusEl = document.getElementById('cmStatus')
    statusEl.textContent = ROTULOS_STATUS[p.status] || p.status || '–'
    statusEl.className = 'selo status-' + (p.status || 'X')
    texto('cmProcedimento', p.procedimento || '–')
    texto('cmProfissional', p.profissional || '–')
    texto('cmData', e.start ? e.start.toLocaleString('pt-BR', { dateStyle: 'full', timeStyle: 'short' }) : '–')
    texto('cmTelefone', p.cliente_telefone || '–')
    texto('cmValor', p.valor ? p.valor.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }) : '–')
    modal.showModal()
  }

  function moverEvento(info) {
    if (!confirm('Reagendar para ' + info.event.start.toLocaleString('pt-BR') + '?')) {
      info.revert()
      return
    }
    const fim = info.event.end || info.event.start
    postJson(urlMover, { id: info.event.id, start: info.event.start.toISOString(), end: fim.toISOString() })
      .then((data) => {
        if (!data.ok) {
          alert('Erro: ' + (data.mensagem || 'Falha ao mover'))
          info.revert()
        }
      })
      .catch(() => { alert('Erro de rede.'); info.revert() })
  }

  const calendar = new Calendar(calendarEl, {
    plugins: [dayGridPlugin, timeGridPlugin, listPlugin, interactionPlugin],
    locale: ptBrLocale,
    buttonHints: { prev: 'Período anterior', next: 'Próximo período', today: 'Ir para hoje' },
    datesSet: ajustarAcessibilidade,
    viewDidMount: ajustarAcessibilidade,
    eventsSet: () => requestAnimationFrame(ajustarAcessibilidade),
    // celular: lista da semana (a grade de 7 colunas nao cabe em 375px)
    initialView: celular ? 'listWeek' : 'timeGridWeek',
    headerToolbar: celular
      ? { left: 'prev,next', center: 'title', right: 'listWeek,timeGridDay' }
      : { left: 'prev,next today', center: 'title', right: 'dayGridMonth,timeGridWeek,timeGridDay' },
    nowIndicator: true,
    editable: true,
    eventDurationEditable: false,
    slotMinTime: '07:00:00',
    slotMaxTime: '21:00:00',
    slotDuration: '00:30:00',
    height: celular ? 'auto' : 720,
    expandRows: true,
    events: eventsSource,
    eventClick: showEvent,
    eventDrop: moverEvento,
  })
  calendar.render()
  calendarEl.calendario = calendar   // testes no navegador conferem a instancia

  filtroProfissional.addEventListener('change', () => calendar.refetchEvents())
  window.addEventListener('resize', () => requestAnimationFrame(ajustarAcessibilidade))
}

if (calendarEl) iniciar()
