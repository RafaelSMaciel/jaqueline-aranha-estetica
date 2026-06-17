import Alpine from 'alpinejs'
import 'htmx.org'
import '../css/app.css'

window.alternarTema = function () {
  const atual = document.documentElement.dataset.theme === 'escuro' ? 'escuro' : 'claro'
  const novo = atual === 'escuro' ? 'claro' : 'escuro'
  document.documentElement.dataset.theme = novo
  localStorage.setItem('tema', novo)
  document.cookie = `tema=${novo}; path=/; max-age=31536000; samesite=lax`
}

window.Alpine = Alpine
Alpine.start()
