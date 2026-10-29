// requisicao.js — POST JSON das telas do painel (status, calendario).
//
// - Token CSRF: <meta name="csrf-token"> da casca do painel (o cookie csrftoken e HttpOnly).
// - Resposta no contrato de utils/respostas: {ok: true, ...} ou {ok: false, codigo, mensagem}.
//   O front decide pelo `codigo`; a `mensagem` e para a pessoa.

export function tokenCsrf() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content) return meta.content;
  const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
  return input ? input.value : '';
}

// Resolve sempre com o corpo JSON do contrato; resposta que nao e JSON (403 do CSRF, 429 do
// ratelimit, 500) vira {ok: false, codigo: 'falha_http', mensagem}.
export async function postJson(url, payload) {
  const resp = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': tokenCsrf() },
    body: JSON.stringify(payload),
  });
  try {
    return await resp.json();
  } catch {
    return { ok: false, codigo: 'falha_http', mensagem: `Falha ao salvar (HTTP ${resp.status}). Tente novamente.` };
  }
}
