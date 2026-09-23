// webpush.js — subscreve usuario admin/profissional para web push.
// Requer:
//  - service worker registrado em '/sw.js' (django route)
//  - endpoints: /webpush/public-key/, /webpush/subscribe/, /webpush/unsubscribe/
//  - meta name="csrf-token" content="{{ csrf_token }}"

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
    if (m) return m.content;
    const c = document.cookie.match(/csrftoken=([^;]+)/);
    return c ? c[1] : '';
  }

  function suportado() {
    return ('serviceWorker' in navigator) && ('PushManager' in window) && ('Notification' in window);
  }

  async function ensureSubscribed() {
    if (!suportado()) return false;
    if (Notification.permission === 'denied') return false;

    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (sub) return true; // ja inscrito

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

    await fetch('/webpush/subscribe/', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
      body: JSON.stringify(sub.toJSON()),
    });
    return true;
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
