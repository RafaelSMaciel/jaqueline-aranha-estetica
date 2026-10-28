// webpush.js — subscreve usuario admin/profissional para web push.
// Requer:
//  - service worker registrado em '/sw.js' (django route)
//  - endpoints: /webpush/public-key/, /webpush/subscribe/, /webpush/unsubscribe/
//  - token CSRF na pagina: meta name="csrf-token" ou qualquer {% csrf_token %}
//    (o cookie csrftoken e HttpOnly: document.cookie nao o enxerga)

(function () {
  'use strict';

  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function getCsrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    if (m && m.content) return m.content;
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input && input.value) return input.value;
    const c = document.cookie.match(/csrftoken=([^;]+)/);
    return c ? c[1] : '';
  }

  const CHAVE_REGISTRO = 'webpush_registrado';

  function lerRegistro() {
    try { return window.sessionStorage.getItem(CHAVE_REGISTRO); } catch (e) { return null; }
  }

  function gravarRegistro(endpoint) {
    try { window.sessionStorage.setItem(CHAVE_REGISTRO, endpoint); } catch (e) { /* storage bloqueado */ }
  }

  // Grava a assinatura no servidor. Falha (403 CSRF, 400, 5xx) PROPAGA: sem isso
  // o botao sumia e o navegador ficava inscrito sem registro no servidor.
  async function registrar(sub) {
    const resp = await fetch('/webpush/subscribe/', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
      body: JSON.stringify(sub.toJSON()),
    });
    if (!resp.ok) throw new Error('webpush subscribe ' + resp.status);
    gravarRegistro(sub.endpoint);
    return true;
  }

  function suportado() {
    return ('serviceWorker' in navigator) && ('PushManager' in window) && ('Notification' in window);
  }

  async function ensureSubscribed() {
    if (!suportado()) return false;
    if (Notification.permission === 'denied') return false;

    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (sub) {
      // Ja inscrito no navegador: re-registra 1x por sessao (idempotente no
      // servidor, update_or_create por endpoint) p/ curar registro que falhou.
      if (lerRegistro() === sub.endpoint) return true;
      return registrar(sub);
    }

    const keyResp = await fetch('/webpush/public-key/', { credentials: 'same-origin' });
    const { public_key: publicKey } = await keyResp.json();
    if (!publicKey) {
      console.warn('Webpush: VAPID public key vazia (env var nao configurada)');
      return false;
    }

    if (Notification.permission === 'default') {
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') return false;
    }

    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    return registrar(sub);
  }

  document.addEventListener('DOMContentLoaded', function () {
    // Gatilho: botao #webpushEnable (painel). Pedir permissao exige gesto do usuario.
    const trigger = document.getElementById('webpushEnable');
    if (trigger) {
      // Sem suporte ou permissao ja decidida: o botao nao tem o que fazer
      if (!suportado() || Notification.permission !== 'default') {
        trigger.remove();
      } else {
        trigger.addEventListener('click', function (e) {
          e.preventDefault();
          ensureSubscribed()
            .then(function () { if (Notification.permission !== 'default') trigger.remove(); })
            .catch(console.warn);
        });
        return;
      }
    }
    // Auto-tenta se ja autorizado e usuario eh staff (template inclui flag)
    if (window.WEBPUSH_AUTO === true && suportado() && Notification.permission === 'granted') {
      ensureSubscribed().catch(console.warn);
    }
  });
})();
