// confirmar.js — dialogo de confirmacao e de aviso do site e do painel (no lugar do confirm()/alert()
// nativos do navegador, que mostravam "localhost diz..." com botoes OK/Cancelar).
//
//   confirmar('Aprovar agendamento de Maria?', { rotulo: 'Aprovar', variante: 'sucesso' }) -> Promise<boolean>
//   avisar('Erro de rede.')                                                               -> Promise<void>
//
// <dialog> nativo com showModal(): foco preso, Esc e clique fora cancelam, fundo inerte.
// O botao de acao leva o NOME da acao ("Aprovar", "Excluir") — nada de "OK". Acao destrutiva
// (variante 'perigo') abre com o foco em "Voltar"; as demais, no botao da acao.
// CSP: nenhum estilo inline; classes de static/src/css/componentes.css (.dialogo-confirmar).

const ICONES = {
  perigo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  sucesso: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
  primario: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  aviso: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
}

let seq = 0

// "Cancelar este pacote? As sessões restantes deixam..." -> titulo = a pergunta, detalhe = o resto
function separar(mensagem) {
  const texto = String(mensagem || '').trim()
  const corte = texto.search(/\?\s+\S/)
  if (corte === -1) return { titulo: texto, detalhe: '' }
  return { titulo: texto.slice(0, corte + 1), detalhe: texto.slice(corte + 1).trim() }
}

function el(tag, classe, texto) {
  const no = document.createElement(tag)
  if (classe) no.className = classe
  if (texto) no.textContent = texto
  return no
}

function abrir({ mensagem, titulo, detalhe, rotulo, rotuloVoltar, variante, soAviso }) {
  return new Promise((resolve) => {
    const partes = titulo ? { titulo, detalhe: detalhe || mensagem || '' } : separar(mensagem)
    if (detalhe && !titulo) partes.detalhe = detalhe
    const id = `confirmar-${++seq}`
    const dlg = el('dialog', 'dialogo dialogo-sm dialogo-confirmar')
    dlg.setAttribute('aria-labelledby', `${id}-titulo`)
    const painel = el('div', 'dialogo-painel')
    const corpo = el('div', 'dialogo-corpo confirmar-corpo')
    const icone = el('span', `confirmar-icone confirmar-icone--${variante}`)
    icone.innerHTML = ICONES[variante] || ICONES.primario   // SVG estatico (sem dado da pessoa)
    const textos = el('div', 'confirmar-textos')
    const h = el('h2', 'dialogo-titulo', partes.titulo)
    h.id = `${id}-titulo`
    textos.appendChild(h)
    if (partes.detalhe) {
      const p = el('p', 'confirmar-detalhe', partes.detalhe)
      p.id = `${id}-detalhe`
      dlg.setAttribute('aria-describedby', p.id)
      textos.appendChild(p)
    }
    corpo.append(icone, textos)
    const rodape = el('div', 'dialogo-rodape')
    const classeAcao = { perigo: 'btn-perigo', sucesso: 'btn-sucesso' }[variante] || 'btn-primario'
    const ok = el('button', `btn ${classeAcao}`, rotulo || (soAviso ? 'Entendi' : 'Confirmar'))
    ok.type = 'button'
    ok.dataset.confirmarOk = ''
    let voltar = null
    if (!soAviso) {
      voltar = el('button', 'btn btn-secundario', rotuloVoltar || 'Voltar')
      voltar.type = 'button'
      voltar.dataset.confirmarVoltar = ''
      rodape.appendChild(voltar)
    }
    rodape.appendChild(ok)
    painel.append(corpo, rodape)
    dlg.appendChild(painel)
    document.body.appendChild(dlg)

    let resposta = false
    const fechar = (valor) => { resposta = valor; dlg.close() }
    ok.addEventListener('click', () => fechar(true))
    if (voltar) voltar.addEventListener('click', () => fechar(false))
    dlg.addEventListener('click', (e) => { if (e.target === dlg) fechar(false) })   // clique fora
    dlg.addEventListener('close', () => { dlg.remove(); resolve(resposta) })
    dlg.showModal()
    ;(variante === 'perigo' && voltar ? voltar : ok).focus()
  })
}

export function confirmar(mensagem, opcoes = {}) {
  return abrir({ mensagem, variante: 'primario', ...opcoes, soAviso: false })
}

export function avisar(mensagem, opcoes = {}) {
  return abrir({ mensagem, variante: 'aviso', ...opcoes, soAviso: true }).then(() => undefined)
}

// Rotulo do botao de acao = o texto visivel do botao clicado ("Aprovar", "Excluir"), sem o
// complemento so p/ leitor de tela (.sr-only) nem icone.
function rotuloDe(gatilho) {
  const copia = gatilho.cloneNode(true)
  copia.querySelectorAll('.sr-only, svg').forEach((n) => n.remove())
  const texto = copia.textContent.replace(/\s+/g, ' ').trim()
  return texto.length > 0 && texto.length <= 32 ? texto : ''
}

function varianteDe(gatilho) {
  if (gatilho.dataset.confirmVariante) return gatilho.dataset.confirmVariante
  const classes = gatilho.className || ''
  if (/perigo/.test(classes)) return 'perigo'
  if (/sucesso/.test(classes)) return 'sucesso'
  return 'primario'
}

// Qualquer elemento com data-confirm="Pergunta?" pede a confirmacao antes de seguir (submit, link
// ou handler proprio). Captura: roda antes dos handlers do elemento. Confirmado, o clique e
// refeito com a marca data-confirmado (passa direto) — o submit leva o name/value do botao.
//   data-confirm-ok="Rotulo"  data-confirm-variante="perigo|sucesso|primario"  (opcionais)
export function ligarDataConfirm() {
  document.addEventListener('click', (e) => {
    const gatilho = e.target.closest('[data-confirm]')
    if (!gatilho) return
    if (gatilho.dataset.confirmado === '1') { delete gatilho.dataset.confirmado; return }
    // campo obrigatorio vazio no formulario: deixa o navegador mostrar a validacao antes de perguntar
    const form = gatilho.form || gatilho.closest('form')
    if (form && gatilho.type === 'submit' && !gatilho.formNoValidate && !form.noValidate && !form.checkValidity()) return
    e.preventDefault()
    e.stopPropagation()
    confirmar(gatilho.getAttribute('data-confirm'), {
      rotulo: gatilho.dataset.confirmOk || rotuloDe(gatilho),
      variante: varianteDe(gatilho),
    }).then((sim) => {
      if (!sim) { gatilho.focus?.(); return }
      gatilho.dataset.confirmado = '1'
      gatilho.click()
    })
  }, true)
}
