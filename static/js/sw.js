'use strict';

const CACHE_NAME = 'tippspiel-v1';
const OFFLINE_URL = '/offline';
const PRECACHE_URLS = ['/offline', '/static/css/style.css', '/static/js/app.js', '/static/manifest.json'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(PRECACHE_URLS).catch(err => {
        console.warn('SW Precache teilweise fehlgeschlagen:', err);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  if (event.request.url.includes('/static/css/') || event.request.url.includes('/static/js/') || event.request.url.includes('/static/uploads/')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        return cached || fetch(event.request).then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          return response;
        });
      })
    );
    return;
  }
});

self.addEventListener('push', event => {
  let data = { title: '⚽ Tippspiel', body: 'Du hast noch nicht getippt!', icon: '/static/uploads/logo_192.png', badge: '/static/uploads/badge_72.png', url: '/schedule', tag: 'tipp-reminder' };
  if (event.data) {
    try { data = { ...data, ...event.data.json() }; } catch (_) { data.body = event.data.text(); }
  }
  const options = {
    body: data.body, icon: data.icon, badge: data.badge, tag: data.tag, renotify: true, requireInteraction: false,
    data: { url: data.url },
    actions: [{ action: 'open', title: '🎯 Jetzt tippen' }, { action: 'dismiss', title: 'Später' }],
  };
  event.waitUntil(self.registration.showNotification(data.title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  if (event.action === 'dismiss') return;
  const targetUrl = event.notification.data?.url || '/schedule';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      for (const client of clientList) { if (client.url.includes(targetUrl) && 'focus' in client) return client.focus(); }
      if (clients.openWindow) return clients.openWindow(targetUrl);
    })
  );
});
