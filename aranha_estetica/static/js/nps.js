/**
 * NPS — seleção de nota via botões data-nota.
 * Sem onclick inline; compatível com CSP strict.
 */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var notaInput  = document.getElementById('nota_input');
    var submitBtn  = document.getElementById('nps_submit');
    var buttons    = document.querySelectorAll('[data-nota]');

    if (!notaInput || !submitBtn || !buttons.length) return;

    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        buttons.forEach(function (b) {
          b.classList.remove('nps-btn--selected');
          b.setAttribute('aria-pressed', 'false');
        });
        btn.classList.add('nps-btn--selected');
        btn.setAttribute('aria-pressed', 'true');
        notaInput.value = btn.dataset.nota;
        submitBtn.disabled = false;
      });
    });
  });
})();
