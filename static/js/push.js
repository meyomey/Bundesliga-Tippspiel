'use strict';

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map(c => c.charCodeAt(0)));
}

function updatePushUI(state) {
  const btn = document.getElementById('push-toggle-btn');
  const status = document.getElementById('push-status-text');
  if (!btn) return;
  if (state === 'subscribed') {
    btn.className = 'btn btn-sm btn-success ms-2';
    btn.innerHTML = '🔔 Push aktiv';
    if (status) status.textContent = 'Push-Benachrichtigungen sind aktiviert.';
  } else if (state === 'denied') {
    btn.className = 'btn btn-sm btn-danger ms-2';
    btn.disabled = true;
    btn.innerHTML = '🚫 Push blockiert';
    if (status) status.textContent = 'Bitte Push in den Browser-Einstellungen erlauben.';
  } else {
    btn.className = 'btn btn-sm btn-outline-primary ms-2';
    btn.innerHTML = '🔕 Push aktivieren';
    if (status) status.textContent = 'Keine Push-Benachrichtigungen.';
  }
}

async function saveSubscription(subscription) {
  const resp = await fetch('/push/subscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '' },
    body: JSON.stringify(subscription.toJSON()),
  });
  if (!resp.ok) throw new Error('Fehler beim Speichern des Abonnements.');
}

async function deleteSubscription() {
  await fetch('/push/unsubscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '' },
  });
}

async function subscribePush() {
  try {
    const reg = await navigator.serviceWorker.ready;
    const vapidKey = window.VAPID_PUBLIC_KEY;
    if (!vapidKey) { alert('VAPID Public Key fehlt. Bitte Admin informieren.'); return; }
    const subscription = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(vapidKey) });
    await saveSubscription(subscription);
    updatePushUI('subscribed');
    console.log('✅ Push-Abo gespeichert.');
  } catch (err) {
    console.error('Push-Fehler:', err);
    if (Notification.permission === 'denied') updatePushUI('denied');
  }
}

async function unsubscribePush() {
  try {
    const reg = await navigator.serviceWorker.ready;
    const subscription = await reg.pushManager.getSubscription();
    if (subscription) { await subscription.unsubscribe(); await deleteSubscription(); }
    updatePushUI('unsubscribed');
    console.log('🔕 Push-Abo entfernt.');
  } catch (err) { console.error('Unsubscribe-Fehler:', err); }
}

async function togglePush() {
  const reg = await navigator.serviceWorker.ready;
  const existing = await reg.pushManager.getSubscription();
  if (existing) await unsubscribePush(); else await subscribePush();
}

async function initPush() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    const btn = document.getElementById('push-toggle-btn');
    if (btn) { btn.disabled = true; btn.title = 'Dein Browser unterstützt kein Push.'; }
    return;
  }
  const reg = await navigator.serviceWorker.ready;
  const subscription = await reg.pushManager.getSubscription();
  const permission = Notification.permission;
  if (permission === 'denied') updatePushUI('denied');
  else if (subscription) updatePushUI('subscribed');
  else updatePushUI('unsubscribed');
  const btn = document.getElementById('push-toggle-btn');
  if (btn) btn.addEventListener('click', togglePush);
}

document.addEventListener('DOMContentLoaded', initPush);
