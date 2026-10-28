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
// Sem escolha salva o tema segue o sistema: o "atual" e o que a pessoa esta vendo.
Alpine.data('temaToggle', () => ({
  alternar() {
    const escolhido = document.documentElement.dataset.theme
    const atual = escolhido || (matchMedia('(prefers-color-scheme: dark)').matches ? 'escuro' : 'claro')
    aplicarTema(atual === 'escuro' ? 'claro' : 'escuro')
  },
}))

// Modal = <dialog x-ref="dialogo"> nativo: foco preso, Esc fecha, fundo inerte (showModal).
// Clique no backdrop (fora do painel) fecha. Os componentes abaixo herdam este comportamento.
// data-erro no x-data: o servidor recusou o envio e devolveu a tela com o que foi digitado ->
// o modal ja abre, com o foco no primeiro campo recusado (aria-invalid) ou no resumo do erro.
const comDialogo = (extra = {}) => ({
  aberto: false,
  init() { if (this.$el.dataset.erro) this.$nextTick(() => this.abrirSeErro()) },
  abrirSeErro() {
    const dlg = this.$refs.dialogo
    if (!dlg) return
    this.aberto = true
    dlg.showModal()
    const alvo = dlg.querySelector('[aria-invalid="true"]') || dlg.querySelector('[data-resumo-erro]')
    alvo?.focus()
  },
  abrir() { this.aberto = true; this.$refs.dialogo?.showModal() },
  fechar() { this.$refs.dialogo?.close(); this.aberto = false },
  aoFechar() { this.aberto = false },
  cliqueFora(e) { if (e.target === this.$refs.dialogo) this.fechar() },
  ...extra,
})

Alpine.data('modal', () => comDialogo())

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
// navInerte(): no celular, menu fechado fica fora da tela -> inert (sai da ordem do Tab e do
// leitor de tela); no desktop (lg, >= 1024px) o menu e fixo e sempre navegavel.
const CELULAR = '(max-width: 1023px)'
Alpine.data('adminShell', () => ({
  menuAberto: false,
  celular: false,
  init() {
    const mq = matchMedia(CELULAR)
    this.celular = mq.matches
    mq.addEventListener('change', (e) => { this.celular = e.matches; if (!e.matches) this.menuAberto = false })
  },
  alternarMenu() {
    this.menuAberto = !this.menuAberto
    // abriu: foco no 1o link do menu (sem isso o Tab seguia pelo conteudo atras do backdrop)
    if (this.menuAberto) this.$nextTick(() => document.querySelector('#sidenavMain a')?.focus())
  },
  fecharMenu() { this.menuAberto = false },
  fecharMenuTeclado() {
    if (!this.menuAberto) return
    this.menuAberto = false
    document.querySelector('[aria-controls="sidenavMain"]')?.focus()
  },
  classeDrawer() { return this.menuAberto ? '!translate-x-0' : '' },
  navInerte() { return this.celular && !this.menuAberto },
}))

// Fecha o alerta pai (CSP-safe: logica no metodo, nao inline em x-on).
Alpine.data('alertaDismiss', () => ({
  fechar() { const a = this.$el.closest('[data-toast], [role=alert]'); if (a) a.remove() },
}))

// Rotulo sem `for` logo antes do campo (padrao dos formularios do painel): associa label -> campo
// (o leitor de tela le o nome do campo e clicar no rotulo foca o campo). Id gerado e unico mesmo
// em lista (um modal de edicao por item).
let seqCampo = 0
function associarRotulos(raiz = document) {
  raiz.querySelectorAll('label:not([for])').forEach((label) => {
    if (label.querySelector('input, select, textarea')) return   // ja envolve o campo
    // o campo e o proximo irmao; uma dica (<p>/<small>) no meio vira a descricao do campo
    const dicas = []
    let campo = label.nextElementSibling
    while (campo && dicas.length < 2 && campo.matches('p, small, span')) { dicas.push(campo); campo = campo.nextElementSibling }
    if (!campo || !campo.matches('input:not([type=hidden]), select, textarea')) return
    if (!campo.id) campo.id = `campo-${++seqCampo}`
    label.htmlFor = campo.id
    if (dicas.length && !campo.hasAttribute('aria-describedby')) {
      campo.setAttribute('aria-describedby', dicas.map((d) => d.id || (d.id = `dica-${++seqCampo}`)).join(' '))
    }
  })
}
document.addEventListener('DOMContentLoaded', () => associarRotulos())

// Formulario fora de modal devolvido com erro: foco no primeiro campo recusado (o modal cuida do seu)
document.addEventListener('DOMContentLoaded', () => {
  const recusado = document.querySelector('form [aria-invalid="true"]')
  if (recusado && !recusado.closest('dialog')) recusado.focus()
})

// Modal de anotacao clinica (prontuario): guarda atendimento e faz fetch.
// CSP-safe: abrir(e) le data-atendimento-id do botao clicado; sem expressao inline.
Alpine.data('anotacaoModal', () => comDialogo({
  atendId: null,
  erro: '',
  abrir(e) {
    const b = e.target.closest('[data-atendimento-id]')
    this.atendId = b ? b.getAttribute('data-atendimento-id') : null
    this.erro = ''
    this.aberto = true
    this.$refs.dialogo?.showModal()
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
Alpine.data('pacoteCriar', () => comDialogo({
  addItem() {
    const c = this.$refs.itens
    const base = c.querySelector('.item-row')
    const row = base.cloneNode(true)
    const sel = row.querySelector('select')
    const inp = row.querySelector('input')
    if (sel) {
      sel.value = ''
      sel.removeAttribute('aria-invalid')   // a marca de erro e da 1a linha, nao da copia
      sel.removeAttribute('aria-describedby')
    }
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
