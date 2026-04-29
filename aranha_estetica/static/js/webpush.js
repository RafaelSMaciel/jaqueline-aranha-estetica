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

  async function ensureSubscribed() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    if (Notification.permission === 'denied') return;

    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (sub) return; // ja inscrito

    const keyResp = await fetch('/webpush/public-key/', { credentials: 'same-origin' });
    const { public_key: publicKey } = await keyResp.json();
    if (!publicKey) {
      console.warn('Webpush: VAPID public key vazia (env var nao configurada)');
      return;
    }

    if (Notification.permission === 'default') {
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') return;
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
  }

  document.addEventListener('DOMContentLoaded', function () {
    const trigger = document.getElementById('webpushEnable');
    if (trigger) {
      trigger.addEventListener('click', function (e) {
        e.preventDefault();
        ensureSubscribed().catch(console.warn);
      });
      return;
    }
    // Auto-tenta se ja autorizado e usuario eh staff (template inclui flag)
    if (window.WEBPUSH_AUTO === true && Notification.permission === 'granted') {
      ensureSubscribed().catch(console.warn);
    }
  });
})();
