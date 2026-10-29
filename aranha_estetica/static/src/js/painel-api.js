// painel-api.js — Swagger UI da documentacao da API (painel/api.html).
//
// A biblioteca vem do npm (swagger-ui-dist, empacotada pelo Vite: nada de CDN na CSP) e le o
// openapi.yaml da URL em data-url-spec. "Try it out" so para GET: as escritas mudam dados reais.
// Sem validador externo (validatorUrl: null). Sem deep link: o link dentro do botao de cada
// operacao seria controle interativo aninhado (axe nested-interactive).
import SwaggerUIBundle from 'swagger-ui-dist/swagger-ui-es-bundle.js'
import 'swagger-ui-dist/swagger-ui.css'
import '../css/painel-api.css'

const alvo = document.getElementById('swagger')

// Tema escuro do proprio Swagger UI (html.dark-mode) acompanha o do painel: a escolha da pessoa
// (data-theme, tambem quando ela troca pelo botao) ou, sem escolha, o do sistema.
const raiz = document.documentElement
const sistemaEscuro = window.matchMedia('(prefers-color-scheme: dark)')
function sincronizarTema() {
  const escolhido = raiz.dataset.theme
  raiz.classList.toggle('dark-mode', escolhido ? escolhido === 'escuro' : sistemaEscuro.matches)
}

if (alvo) {
  sincronizarTema()
  sistemaEscuro.addEventListener('change', sincronizarTema)
  new MutationObserver(sincronizarTema).observe(raiz, { attributes: true, attributeFilter: ['data-theme'] })

  SwaggerUIBundle({
    url: alvo.dataset.urlSpec,
    domNode: alvo,
    deepLinking: false,
    docExpansion: 'list',
    defaultModelsExpandDepth: 0,
    displayRequestDuration: true,
    filter: true,
    supportedSubmitMethods: ['get'],
    validatorUrl: null,
    tryItOutEnabled: false,
    // so GET pode ser experimentado (sem CSRF); o cookie de sessao vai junto (mesma origem)
    requestInterceptor: (req) => {
      req.credentials = 'same-origin'
      return req
    },
  })
}
