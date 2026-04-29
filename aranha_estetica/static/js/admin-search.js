// admin-search.js — filtro instantaneo client-side para listas em cards.
// Uso:
//   <input data-search-filter="#minha-lista" placeholder="Buscar...">
//   <div id="minha-lista">
//     <div class="card-row" data-search-text="texto a indexar">...</div>
//   </div>
// Ou:
//   <div id="lista" data-search-rows=".cliente-card"> ... </div>
// data-search-text e' o texto pesquisavel (ja minusculo). Se ausente, usa textContent.

(function () {
  'use strict';

  function normalize(s) {
    return (s || '').toString().toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  }

  function indexRow(row) {
    if (row.dataset.searchIndexed) return;
    var src = row.dataset.searchText || row.textContent || '';
    row.dataset.searchIndex = normalize(src);
    row.dataset.searchIndexed = '1';
  }

  function attachFilter(input) {
    var sel = input.dataset.searchFilter;
    var container = sel ? document.querySelector(sel) : null;
    if (!container) return;
    var rowsSel = container.dataset.searchRows;
    var counterEl = input.dataset.searchCounter ? document.querySelector(input.dataset.searchCounter) : null;
    var emptyEl = input.dataset.searchEmpty ? document.querySelector(input.dataset.searchEmpty) : null;

    function getRows() {
      return rowsSel ? container.querySelectorAll(rowsSel) : container.children;
    }

    function applyFilter() {
      var q = normalize(input.value.trim());
      var rows = getRows();
      var visible = 0;
      Array.prototype.forEach.call(rows, function (row) {
        if (row.nodeType !== 1) return;
        indexRow(row);
        var match = !q || row.dataset.searchIndex.indexOf(q) !== -1;
        row.style.display = match ? '' : 'none';
        if (match) visible++;
      });
      if (counterEl) counterEl.textContent = visible;
      if (emptyEl) emptyEl.style.display = visible === 0 && q ? '' : 'none';
    }

    var t;
    input.addEventListener('input', function () {
      clearTimeout(t);
      t = setTimeout(applyFilter, 80);
    });
    applyFilter();
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('input[data-search-filter]').forEach(attachFilter);
  });
})();
