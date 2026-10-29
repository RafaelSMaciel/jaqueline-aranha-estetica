// painel.js — comportamento dos componentes do design system nas telas da equipe (painel e portal
// da profissional). Vanilla e por delegacao no document: nada inline (CSP com nonce) e nada que
// dependa do Alpine (o app.js segue dono do Alpine). Sem JS tudo continua utilizavel: as abas sao
// links de ancora p/ secoes visiveis, o menu "⋯" e um <details> nativo.

// ── Abas (cotton/abas.html): padrao WAI-ARIA Tabs com ativacao automatica ──
function iniciarAbas(raiz) {
  const lista = raiz.querySelector('[data-abas-lista]')
  const abas = Array.from(lista.querySelectorAll('[data-aba]'))
  const paineis = abas.map((a) => document.getElementById(a.dataset.aba)).filter(Boolean)
  if (!abas.length || paineis.length !== abas.length) return

  lista.setAttribute('role', 'tablist')
  abas.forEach((aba, i) => {
    aba.setAttribute('role', 'tab')
    aba.setAttribute('aria-controls', paineis[i].id)
    paineis[i].setAttribute('role', 'tabpanel')
    paineis[i].setAttribute('tabindex', '0')
  })

  // ultima aba desta pagina (sessionStorage, so conveniencia): um POST que volta por redirect
  // (sem #hash) reabre a aba onde a pessoa estava, nao a primeira
  const chave = 'aba:' + location.pathname
  function lembrar(id) { try { sessionStorage.setItem(chave, id) } catch (e) { /* modo privado */ } }
  function lembrada() {
    try { return abas.find((a) => a.dataset.aba === sessionStorage.getItem(chave)) || null } catch (e) { return null }
  }

  function selecionar(alvo, { foco = false, hash = true } = {}) {
    abas.forEach((aba, i) => {
      const ativa = aba === alvo
      aba.setAttribute('aria-selected', String(ativa))
      aba.setAttribute('tabindex', ativa ? '0' : '-1')
      paineis[i].hidden = !ativa
    })
    if (foco) alvo.focus()
    if (hash) history.replaceState(null, '', '#' + alvo.dataset.aba)
    lembrar(alvo.dataset.aba)
  }

  lista.addEventListener('click', (e) => {
    const aba = e.target.closest('[data-aba]')
    if (!aba) return
    e.preventDefault()
    selecionar(aba)
  })
  lista.addEventListener('keydown', (e) => {
    const i = abas.indexOf(document.activeElement)
    if (i < 0) return
    const destino = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: abas.length - 1 }[e.key]
    if (destino === undefined) return
    e.preventDefault()
    selecionar(abas[(destino + abas.length) % abas.length], { foco: true })
  })

  // aba inicial: a do #hash (link direto ou volta de um POST), senao a primeira; um #ancora de
  // algo DENTRO de um painel abre o painel que o contem
  function pelaUrl() {
    const id = decodeURIComponent(location.hash.slice(1))
    const alvoHash = id && document.getElementById(id)
    const painel = alvoHash && paineis.find((p) => p === alvoHash || p.contains(alvoHash))
    return painel ? abas[paineis.indexOf(painel)] : null
  }
  selecionar(pelaUrl() || lembrada() || abas[0], { hash: false })
  window.addEventListener('hashchange', () => { const a = pelaUrl(); if (a) selecionar(a, { hash: false }) })
  // erro de formulario devolvido pelo servidor dentro de um painel escondido: abre o painel
  const recusado = raiz.querySelector('[aria-invalid="true"]')
  const painelErro = recusado && paineis.find((p) => p.contains(recusado))
  if (painelErro) selecionar(abas[paineis.indexOf(painelErro)], { hash: false })
}

// ── Menu "⋯" (cotton/menu_acoes.html, <details data-menu-acoes>) ──
function fecharMenus(exceto) {
  document.querySelectorAll('details[data-menu-acoes][open]').forEach((d) => { if (d !== exceto) d.open = false })
}
document.addEventListener('toggle', (e) => {
  const d = e.target
  if (d.matches && d.matches('details[data-menu-acoes]') && d.open) fecharMenus(d)
}, true)
document.addEventListener('click', (e) => {
  if (!e.target.closest('details[data-menu-acoes]')) fecharMenus(null)
})
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return
  const aberto = document.querySelector('details[data-menu-acoes][open]')
  if (!aberto || document.querySelector('dialog[open]')) return
  aberto.open = false
  aberto.querySelector('summary').focus()
})

// ── Modais (cotton/dialogo.html): abrir/fechar sem Alpine ──
document.addEventListener('click', (e) => {
  const abrir = e.target.closest('[data-abrir-dialogo]')
  if (abrir) {
    const dlg = document.getElementById(abrir.dataset.abrirDialogo)
    if (dlg && !dlg.open) {
      const menu = abrir.closest('details[data-menu-acoes]')
      if (menu) menu.open = false
      dlg.showModal()
      dlg.querySelector('input:not([type=hidden]), select, textarea')?.focus()
    }
    return
  }
  const fechar = e.target.closest('[data-fechar-dialogo]')
  if (fechar) { fechar.closest('dialog')?.close(); return }
  // clique no fundo (fora do painel) fecha
  if (e.target.matches && e.target.matches('dialog.dialogo[open]')) e.target.close()
})

// ── Busca global da barra do topo: Ctrl+K / Cmd+K foca ──
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
    const busca = document.getElementById('adminGlobalSearch')
    if (busca) { e.preventDefault(); busca.focus() }
  }
})

document.querySelectorAll('[data-abas]').forEach(iniciarAbas)
