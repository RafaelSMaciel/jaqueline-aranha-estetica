// painel-status.js — select de status das listas do painel (partials/_select_status.html) e a
// barra de Aprovar/Recusar em lote da agenda. Antes: duas copias inline (agenda e visao geral).
//
// <select data-atend-id="12" data-url="/painel/atualizar-status/" [data-termo-pendente="1"]>
// Resposta no contrato de utils/respostas: decide pelo `codigo`, mostra a `mensagem`.
// Handler delegado no document (CSP com nonce, nada de on* inline).
import { postJson } from './lib/requisicao.js';

const MSG_SEM_TERMO = 'A cliente ainda não aceitou o termo do procedimento. Marcar como realizado mesmo assim?';

function enviarStatus(sel, payload) {
  postJson(sel.dataset.url, payload)
    .then(function (data) {
      if (data.ok) { location.reload(); return; }
      // Termo publicado depois de abrir a pagina: o servidor recusa com codigo termo_pendente; pede a
      // confirmacao explicita e reenvia com o override (fica na trilha: 'Realizado sem termo aceito')
      if (data.codigo === 'termo_pendente' && !payload.sem_termo) {
        if (confirm(MSG_SEM_TERMO)) {
          payload.sem_termo = true;
          enviarStatus(sel, payload);
        } else { sel.value = ''; }
        return;
      }
      alert('Erro: ' + (data.mensagem || 'Falha ao atualizar'));
      sel.value = '';
    })
    .catch(function () { alert('Erro de rede'); sel.value = ''; });
}

document.addEventListener('change', function (e) {
  const sel = e.target.closest('select[data-atend-id]');
  if (!sel || !sel.value) return;
  const rotulo = sel.options[sel.selectedIndex].text.trim();
  const payload = { atendimento_id: sel.dataset.atendId, status: sel.value };
  if (sel.value === 'REALIZADO' && sel.dataset.termoPendente === '1') {
    // Override explicito (auditado no servidor): termo do procedimento nao aceito
    if (!confirm(MSG_SEM_TERMO)) { sel.value = ''; return; }
    payload.sem_termo = true;
  } else if (!confirm('Alterar o status para "' + rotulo + '"?')) {
    sel.value = '';
    return;
  }
  enviarStatus(sel, payload);
});

// Lote: a barra aparece com >= 1 pendente marcado (checkbox .bulk-checkbox, form="bulkForm")
(function () {
  const toolbar = document.getElementById('bulkToolbar');
  if (!toolbar) return;
  const counter = document.getElementById('bulkCount');
  const selectAll = document.getElementById('bulkSelectAll');
  const limpar = document.getElementById('bulkClear');
  const caixas = function () { return document.querySelectorAll('.bulk-checkbox'); };

  function atualizar() {
    const marcados = document.querySelectorAll('.bulk-checkbox:checked').length;
    toolbar.style.display = marcados > 0 ? 'flex' : 'none';
    if (marcados > 0) counter.textContent = marcados + ' selecionado(s)';
  }

  document.addEventListener('change', function (e) {
    if (e.target.classList.contains('bulk-checkbox')) atualizar();
  });
  if (selectAll) {
    selectAll.addEventListener('change', function () {
      caixas().forEach(function (c) { c.checked = selectAll.checked; });
      atualizar();
    });
  }
  if (limpar) {
    limpar.addEventListener('click', function () {
      caixas().forEach(function (c) { c.checked = false; });
      if (selectAll) selectAll.checked = false;
      atualizar();
    });
  }
})();
