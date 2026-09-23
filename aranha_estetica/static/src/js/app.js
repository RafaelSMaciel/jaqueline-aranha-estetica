import Alpine from '@alpinejs/csp'
// HTMX removido: sem nenhum hx-* no projeto e injetava <style> sem nonce (CSP).
import '../css/app.css'

function aplicarTema(novo) {
  document.documentElement.dataset.theme = novo
  localStorage.setItem('tema', novo)
  let cookie = `tema=${novo}; path=/; max-age=31536000; samesite=lax`
  if (location.protocol === 'https:') cookie += '; secure'
  document.cookie = cookie
}

// Componente CSP-safe (sem expressao JS inline em atributo).
// Uso na casca: <button x-data="temaToggle" x-on:click="alternar">
Alpine.data('temaToggle', () => ({
  alternar() {
    const atual = document.documentElement.dataset.theme === 'escuro' ? 'escuro' : 'claro'
    aplicarTema(atual === 'escuro' ? 'claro' : 'escuro')
  },
}))

Alpine.data('modal', () => ({
  aberto: false,
  abrir() { this.aberto = true },
  fechar() { this.aberto = false },
}))

// Nav do site publico: toggle do menu mobile (CSP-safe).
Alpine.data('navMenu', () => ({
  aberto: false,
  alternar() { this.aberto = !this.aberto },
  fechar() { this.aberto = false },
}))

// Banner de consentimento de cookies (LGPD). Grava a escolha em cookie de 1 ano
// (a sessao expira em 30 min e o banner voltava), registra no servidor e some.
// CSP-safe: URL/CSRF vem de data-attrs do $root; sem expressao inline.
Alpine.data('cookieConsent', () => ({
  enviar(analytics, marketing) {
    let cookie = `cookie_consent=${analytics || marketing ? 'todos' : 'essenciais'}; path=/; max-age=31536000; samesite=lax`
    if (location.protocol === 'https:') cookie += '; secure'
    document.cookie = cookie
    const fd = new FormData()
    fd.append('analytics', analytics ? '1' : '0')
    fd.append('marketing', marketing ? '1' : '0')
    fd.append('csrfmiddlewaretoken', this.$root.dataset.csrf)
    fetch(this.$root.dataset.url, {
      method: 'POST', body: fd, headers: { 'X-CSRFToken': this.$root.dataset.csrf },
    }).catch(() => {}).finally(() => { this.$root.remove() })
  },
  aceitarTodos() { this.enviar(true, true) },
  soEssenciais() { this.enviar(false, false) },
}))

// App-shell do painel admin: sidenav drawer mobile + backdrop.
// classeDrawer() = metodo (CSP build nao aceita ternario inline em x-bind).
Alpine.data('adminShell', () => ({
  menuAberto: false,
  alternarMenu() { this.menuAberto = !this.menuAberto },
  fecharMenu() { this.menuAberto = false },
  classeDrawer() { return this.menuAberto ? '!translate-x-0' : '' },
}))

// Fecha o alerta pai (CSP-safe: logica no metodo, nao inline em x-on).
Alpine.data('alertaDismiss', () => ({
  fechar() { const a = this.$el.closest('[role=alert]'); if (a) a.remove() },
}))

// Modal de anotacao clinica (prontuario): guarda atendimento e faz fetch.
// CSP-safe: abrir(e) le data-atendimento-id do botao clicado; sem expressao inline.
Alpine.data('anotacaoModal', () => ({
  aberto: false,
  atendId: null,
  erro: '',
  fechar() { this.aberto = false },
  abrir(e) {
    const b = e.target.closest('[data-atendimento-id]')
    this.atendId = b ? b.getAttribute('data-atendimento-id') : null
    this.erro = ''
    this.aberto = true
  },
  salvar() {
    const ta = this.$refs.texto
    const texto = (ta.value || '').trim()
    if (!texto) return
    const fd = new FormData()
    fd.append('texto', texto)
    fd.append('csrfmiddlewaretoken', this.$root.dataset.csrf)
    fetch('/painel/anotacao/' + this.atendId + '/salvar/', { method: 'POST', body: fd })
      .then((r) => r.json())
      .then((d) => { if (d.sucesso) location.reload(); else { this.erro = d.erro || 'Erro ao salvar.' } })
      .catch(() => { this.erro = 'Erro de conexao.' })
  },
}))

// Modal de criar pacote: estado do modal + clonar linha de item (CSP-safe).
Alpine.data('pacoteCriar', () => ({
  aberto: false,
  abrir() { this.aberto = true },
  fechar() { this.aberto = false },
  addItem() {
    const c = this.$refs.itens
    const base = c.querySelector('.item-row')
    const row = base.cloneNode(true)
    const sel = row.querySelector('select')
    const inp = row.querySelector('input')
    if (sel) sel.value = ''
    if (inp) inp.value = '1'
    c.appendChild(row)
  },
}))

// Confirmacao CSP-safe global: qualquer elemento com data-confirm pede confirm()
// antes de submeter/seguir. Substitui onclick/onsubmit inline (proibidos no CSP build).
document.addEventListener('click', function (e) {
  const el = e.target.closest('[data-confirm]')
  if (el && !window.confirm(el.getAttribute('data-confirm'))) {
    e.preventDefault()
    e.stopPropagation()
  }
})

// ── Scroll-reveal (vanilla, CSP-safe) ──
// Elementos .reveal sobem/fade ao entrar na viewport, com stagger entre irmaos.
function initRevelar() {
  const els = document.querySelectorAll('.reveal')
  if (!els.length) return
  const semMovimento = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (semMovimento || !('IntersectionObserver' in window)) {
    els.forEach((el) => el.classList.add('is-visible'))
    return
  }
  const io = new IntersectionObserver((entries, obs) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return
      const el = e.target
      const irmaos = Array.from(el.parentElement.children).filter((c) => c.classList.contains('reveal'))
      const i = irmaos.indexOf(el)
      if (i > 0) el.style.transitionDelay = Math.min(i, 6) * 70 + 'ms'
      el.classList.add('is-visible')
      obs.unobserve(el)
    })
  }, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' })
  els.forEach((el) => io.observe(el))
}

// ── Botao voltar ao topo (vanilla) ──
function initVoltarTopo() {
  const btn = document.getElementById('btnTopo')
  if (!btn) return
  const aoRolar = () => { btn.classList.toggle('show', window.scrollY > 500) }
  window.addEventListener('scroll', aoRolar, { passive: true })
  aoRolar()
  btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }))
}

initRevelar()
initVoltarTopo()

window.Alpine = Alpine
Alpine.start()
