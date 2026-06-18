import Alpine from '@alpinejs/csp'
import 'htmx.org'
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

window.Alpine = Alpine
Alpine.start()
