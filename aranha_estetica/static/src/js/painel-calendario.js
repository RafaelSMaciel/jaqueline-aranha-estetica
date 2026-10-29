// painel-calendario.js — FullCalendar do painel (painel/calendar.html).
//
// A biblioteca vem do CDN (script classico com SRI e nonce, antes deste modulo). Daqui saem:
// eventos por AJAX (data-url-eventos), arrasto para reagendar (data-url-mover, resposta no
// contrato de utils/respostas) e o modal do atendimento. Sem handler inline (CSP com nonce).
import { postJson } from './lib/requisicao.js';

const calendarEl = document.getElementById('calendar');

// Setas do FullCalendar: o <span class="fc-icon" role="img"> sai sem nome (axe role-img-alt).
// O nome fica no botao (aria-label) e o icone vira decorativo. Reaplica a cada troca de periodo.
const NOMES_SETAS = { 'fc-prev-button': 'Período anterior', 'fc-next-button': 'Próximo período' };
function nomearSetas() {
  Object.keys(NOMES_SETAS).forEach(function (cls) {
    calendarEl.querySelectorAll('.' + cls).forEach(function (b) { b.setAttribute('aria-label', NOMES_SETAS[cls]); });
  });
  calendarEl.querySelectorAll('.fc-icon').forEach(function (i) { i.setAttribute('aria-hidden', 'true'); });
}

function iniciar() {
  const filtroProfissional = document.getElementById('filtroProfissional');
  const modal = document.getElementById('calModal');
  const celular = window.matchMedia('(max-width: 640px)').matches;
  const urlEventos = calendarEl.dataset.urlEventos;
  const urlMover = calendarEl.dataset.urlMover;

  function eventsSource(info, success, failure) {
    let url = urlEventos + '?start=' + encodeURIComponent(info.startStr) + '&end=' + encodeURIComponent(info.endStr);
    if (filtroProfissional.value) url += '&profissional=' + encodeURIComponent(filtroProfissional.value);
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(success)
      .catch(failure);
  }

  function texto(id, valor) { document.getElementById(id).textContent = valor; }

  function showEvent(info) {
    const e = info.event;
    const p = e.extendedProps || {};
    if (!p.status) return;   // bloqueio/folga (evento de fundo): nao ha atendimento p/ mostrar
    texto('cmClienteNome', p.cliente_nome || e.title);
    const statusEl = document.getElementById('cmStatus');
    statusEl.textContent = p.status || '–';
    statusEl.className = 'status-pill status-' + (p.status || 'X');
    texto('cmProcedimento', p.procedimento || '–');
    texto('cmProfissional', p.profissional || '–');
    texto('cmData', e.start ? e.start.toLocaleString('pt-BR') : '–');
    texto('cmTelefone', p.cliente_telefone || '–');
    texto('cmValor', p.valor ? 'R$ ' + p.valor.toFixed(2).replace('.', ',') : '–');
    modal.showModal();
  }

  function moverEvento(info) {
    if (!confirm('Reagendar para ' + info.event.start.toLocaleString('pt-BR') + '?')) {
      info.revert();
      return;
    }
    const fim = info.event.end || info.event.start;
    postJson(urlMover, { id: info.event.id, start: info.event.start.toISOString(), end: fim.toISOString() })
      .then(function (data) {
        if (!data.ok) {
          alert('Erro: ' + (data.mensagem || 'Falha ao mover'));
          info.revert();
        }
      })
      .catch(function () { alert('Erro de rede.'); info.revert(); });
  }

  const calendar = new window.FullCalendar.Calendar(calendarEl, {
    locale: 'pt-br',
    buttonHints: { prev: 'Período anterior', next: 'Próximo período', today: 'Ir para hoje' },
    datesSet: nomearSetas,
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
    height: 700,
    events: eventsSource,
    eventClick: showEvent,
    eventDrop: moverEvento,
  });
  calendar.render();

  filtroProfissional.addEventListener('change', function () { calendar.refetchEvents(); });
  document.getElementById('cmFechar').addEventListener('click', function () { modal.close(); });
  modal.addEventListener('click', function (e) { if (e.target === modal) modal.close(); });
}

// Modulo roda depois do parse (defer); o FullCalendar do CDN ja carregou (script classico antes).
if (calendarEl && window.FullCalendar) iniciar();
