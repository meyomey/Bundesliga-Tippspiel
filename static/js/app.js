// =====================================================
// Wulmstörper Tipprunde - Frontend Interactions
// =====================================================

// ==== Theme Toggle ====
function toggleTheme() {
  const html = document.documentElement;
  const current = html.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
}
const savedTheme = localStorage.getItem('theme');
if (savedTheme) document.documentElement.setAttribute('data-theme', savedTheme);

// ==== Mobile Hamburger Menu ====
document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('navToggle');
  const menu = document.getElementById('navMenu');

  function closeMenu() {
    menu?.classList.remove('open');
    toggle?.classList.remove('is-open');
    toggle?.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('nav-open');
  }
  function openMenu() {
    menu?.classList.add('open');
    toggle?.classList.add('is-open');
    toggle?.setAttribute('aria-expanded', 'true');
    document.body.classList.add('nav-open');
  }

  if (toggle && menu) {
    toggle.addEventListener('click', () => {
      menu.classList.contains('open') ? closeMenu() : openMenu();
    });
    // Schließen beim Link-Klick
    menu.querySelectorAll('a').forEach(a => {
      a.addEventListener('click', closeMenu);
    });
    // Schließen mit ESC
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && menu.classList.contains('open')) closeMenu();
    });
    // Auto-Close beim Resize zur Desktop-Breite
    const mq = window.matchMedia('(min-width: 1024px)');
    mq.addEventListener('change', e => { if (e.matches) closeMenu(); });
  }

  // ==== Quick-Tip Joker visuelles Feedback ====
  document.querySelectorAll('input[name="joker_match"]').forEach(radio => {
    radio.addEventListener('change', () => {
      // Alle Joker-Labels zurücksetzen
      document.querySelectorAll('.qtip-joker-label').forEach(l => l.classList.remove('has-joker'));
      document.querySelectorAll('.qtip-row').forEach(r => r.classList.remove('has-joker'));
      if (radio.checked) {
        const label = radio.closest('label');
        const row = radio.closest('.qtip-row');
        if (label) label.classList.add('has-joker');
        if (row) row.classList.add('has-joker');
      }
    });
    // Initial state
    if (radio.checked) {
      const label = radio.closest('label');
      const row = radio.closest('.qtip-row');
      if (label) label.classList.add('has-joker');
      if (row) row.classList.add('has-joker');
    }
  });

  // ==== "Mehr"-Dropdown im Desktop-Menu ====
  const moreBtn = document.getElementById('navMoreBtn');
  const moreMenu = document.getElementById('navMoreMenu');
  const moreWrapper = moreBtn?.closest('.nav-more');
  if (moreBtn && moreWrapper) {
    moreBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = moreWrapper.classList.toggle('open');
      moreBtn.setAttribute('aria-expanded', open);
    });
    document.addEventListener('click', (e) => {
      if (!moreWrapper.contains(e.target)) {
        moreWrapper.classList.remove('open');
        moreBtn.setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        moreWrapper.classList.remove('open');
        moreBtn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  // ==== Player-Tooltip Portal-Pattern ====
  // Tooltips werden ans <body> umgehängt, damit sie NICHT von Container-
  // overflow:hidden (z.B. .card.flat) abgeschnitten werden.
  let activeTooltip = null;

  function hideActiveTooltip() {
    if (activeTooltip) {
      activeTooltip.classList.remove('pt-portal-open');
      activeTooltip = null;
    }
  }

  function showTooltip(trigger) {
    // Tooltip-Referenz: erst im Trigger suchen (1. Mal),
    // dann (sobald per Portal verschoben) aus dem zwischengespeicherten Cache holen.
    let tip = trigger._portalTip;
    if (!tip) {
      tip = trigger.querySelector(':scope > .player-tooltip');
      if (!tip) return;
      trigger._portalTip = tip;          // Referenz behalten
      tip._owner = trigger;               // umgekehrt auch
      document.body.appendChild(tip);    // einmalig portal-en
      tip.classList.add('pt-portal');
    }

    // Falls aktuell ein anderer Tooltip offen → schließen
    if (activeTooltip && activeTooltip !== tip) {
      hideActiveTooltip();
    }
    activeTooltip = tip;

    // Position dynamisch berechnen
    const triggerRect = trigger.getBoundingClientRect();
    // Tooltip kurz "messbar" machen ohne sichtbar zu sein
    tip.style.visibility = 'hidden';
    tip.classList.add('pt-portal-open');
    const tipRect = tip.getBoundingClientRect();

    const vh = window.innerHeight;
    const vw = window.innerWidth;
    const margin = 10;
    const gap = 8;

    // Default: unter Trigger
    let top = triggerRect.bottom + gap;
    let left = triggerRect.left;
    let placement = 'bottom';

    // Unten kein Platz → über Trigger
    if (top + tipRect.height + margin > vh && triggerRect.top - tipRect.height - gap > margin) {
      top = triggerRect.top - tipRect.height - gap;
      placement = 'top';
    }

    // Horizontal: nicht über rechten Rand
    if (left + tipRect.width + margin > vw) {
      left = vw - tipRect.width - margin;
    }
    if (left < margin) left = margin;

    tip.style.top = `${top}px`;
    tip.style.left = `${left}px`;
    tip.dataset.placement = placement;

    // Sichtbar machen
    tip.style.visibility = '';
  }

  document.querySelectorAll('.has-tooltip').forEach(trigger => {
    trigger.addEventListener('mouseenter', () => showTooltip(trigger));
    trigger.addEventListener('mouseleave', hideActiveTooltip);
    trigger.addEventListener('focusin', () => showTooltip(trigger));
    trigger.addEventListener('focusout', hideActiveTooltip);
  });
  // Bei Scroll/Resize: Tooltip schließen
  window.addEventListener('scroll', hideActiveTooltip, { passive: true });
  window.addEventListener('resize', hideActiveTooltip);

  // ==== Service Worker registrieren (am Root-Pfad für volle Scope) ====
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then(reg => {
        console.log('✅ Service Worker registered, scope:', reg.scope);
      })
      .catch(err => {
        console.warn('Service Worker registration failed:', err);
      });
  }

  // ==== PWA-Install-Prompt (Chrome/Edge/Android) ====
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    showInstallButton();
  });

  function showInstallButton() {
    if (document.getElementById('pwaInstallBtn')) return;
    const btn = document.createElement('button');
    btn.id = 'pwaInstallBtn';
    btn.className = 'pwa-install-btn';
    btn.innerHTML = '📱 App installieren';
    btn.title = 'Installiere die Tipprunde auf deinem Gerät';
    btn.addEventListener('click', async () => {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      const { outcome } = await deferredPrompt.userChoice;
      if (outcome === 'accepted') {
        btn.remove();
        if (window.showToast) window.showToast('App wird installiert!', 'ok');
      }
      deferredPrompt = null;
    });
    document.body.appendChild(btn);
  }

  // Wenn schon installiert: Button verstecken
  window.addEventListener('appinstalled', () => {
    const btn = document.getElementById('pwaInstallBtn');
    if (btn) btn.remove();
    if (window.showToast) window.showToast('🎉 App installiert!', 'ok');
  });

  // iOS Safari: kein beforeinstallprompt → Tipp anzeigen
  if (/iPad|iPhone|iPod/.test(navigator.userAgent)
      && !window.navigator.standalone
      && !document.referrer.startsWith('android-app://')) {
    // Nur einmal pro Session zeigen
    if (!sessionStorage.getItem('iosInstallTipShown')) {
      sessionStorage.setItem('iosInstallTipShown', '1');
      setTimeout(() => {
        if (window.showToast) {
          window.showToast(
            '📱 Tipp: Auf "Teilen" → "Zum Home-Bildschirm" tippen für die App!',
            'info', 8000
          );
        }
      }, 3500);
    }
  }

  // ==== Auto-dismiss Flash Messages ====
  setTimeout(() => {
    document.querySelectorAll('.flash').forEach(f => {
      f.style.transition = 'opacity .4s, transform .4s';
      f.style.opacity = '0';
      f.style.transform = 'translateY(-10px)';
      setTimeout(() => f.remove(), 400);
    });
  }, 5000);

  // ====================================================================
  // 🌍 Globale UTC → Lokale Zeit-Konvertierung
  // ====================================================================
  (function convertUTCToLocal() {
    // Suche nach Elementen mit 'data-utc' Attribut
    const elements = document.querySelectorAll('[data-utc]');
    if (elements.length === 0) return;

    const daysDE = ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'];
    
    elements.forEach(container => {
      const utcStr = container.dataset.utc;
      if (!utcStr) return;
      
      const d = new Date(utcStr);
      if (isNaN(d.getTime())) return; // Ungültiges Datum

      const dayName = daysDE[d.getDay()];
      const dd = String(d.getDate()).padStart(2, '0');
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const HH = String(d.getHours()).padStart(2, '0');
      const MM = String(d.getMinutes()).padStart(2, '0');

      // === Schedule (Spiele & Tipps) Struktur ===
      // <div class="match-time" data-utc="...">
      //   <small class="match-date">...</small>
      //   <strong class="match-hour">...</strong>
      // </div>
      const dateEl = container.querySelector('.match-date');
      const timeEl = container.querySelector('.match-hour');
      if (dateEl) dateEl.textContent = `${dayName}, ${dd}.${mm}.`;
      if (timeEl) timeEl.textContent = `${HH}:${MM}`;

      // === Quick-Tip (Schnelltipp) Struktur ===
      // <div class="qt-time" data-utc="...">
      //   <small class="qt-day">...</small>
      //   <strong class="qt-time-val">...</strong>
      //   <small class="qt-date-val">...</small>
      // </div>
      const qtDayEl = container.querySelector('.qt-day');
      const qtTimeEl = container.querySelector('.qt-time-val');
      const qtDateEl = container.querySelector('.qt-date-val');
      if (qtDayEl) qtDayEl.textContent = dayName;
      if (qtTimeEl) qtTimeEl.textContent = `${HH}:${MM}`;
      if (qtDateEl) qtDateEl.textContent = `${dd}.${mm}.`;

      // === Dashboard Struktur ===
      // <div class="match-date" data-utc="...">
      //   <strong class="dash-date-val">...</strong>
      //   <span class="dash-time-val">...</span>
      // </div>
      const dashDateEl = container.querySelector('.dash-date-val');
      const dashTimeEl = container.querySelector('.dash-time-val');
      if (dashDateEl) dashDateEl.textContent = `${dd}.${mm}.`;
      if (dashTimeEl) dashTimeEl.textContent = `${HH}:${MM}`;
    });
  })();

  // ====================================================================
  // Rangliste: "Alle ausklappen / einklappen" Toggle
  // ====================================================================
  (function leaderboardExpandToggle() {
    const btn = document.getElementById('lbExpandAll');
    if (!btn) return;
    const cards = () => document.querySelectorAll('.lb-card[open], .lb-card:not([open])');
    btn.addEventListener('click', () => {
      const allCards = document.querySelectorAll('.lb-card');
      // Wenn mind. eine Karte zu ist → alle aufklappen, sonst alle einklappen
      const anyClosed = Array.from(allCards).some(c => !c.hasAttribute('open'));
      allCards.forEach(c => {
        if (anyClosed) c.setAttribute('open', '');
        else c.removeAttribute('open');
      });
      btn.textContent = anyClosed ? '⤴ Alle einklappen' : '⤵ Alle ausklappen';
    });
  })();

  // ====================================================================
  // FEATURE B: Countdown-Banner für ungetippte Spiele
  // ====================================================================
  const banner = document.getElementById('tipReminderBanner');
  if (banner) {
    const target = new Date(banner.dataset.kickoff);
    const cdEl = document.getElementById('trCountdown');
    function updateBannerCountdown() {
      const diff = target - new Date();
      if (diff <= 0) {
        banner.style.display = 'none';
        return;
      }
      const h = Math.floor(diff / 3600000);
      const m = Math.floor(diff % 3600000 / 60000);
      if (cdEl) {
        if (h >= 24) {
          const d = Math.floor(h / 24);
          cdEl.textContent = `in ${d}d ${h % 24}h`;
        } else if (h >= 1) {
          cdEl.textContent = `in ${h}h ${String(m).padStart(2,'0')}min`;
        } else {
          const s = Math.floor(diff % 60000 / 1000);
          cdEl.textContent = `in ${m}min ${String(s).padStart(2,'0')}s`;
          banner.classList.add('urgent');
        }
      }
    }
    updateBannerCountdown();
    setInterval(updateBannerCountdown, 1000);
  }

  // ====================================================================
  // FEATURE D: Toast-Notification-System
  // ====================================================================
  window.showToast = function(message, type = 'info', duration = 3500) {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const t = document.createElement('div');
    t.className = `toast toast-${type}`;
    t.innerHTML = `<span>${message}</span><button class="toast-close" aria-label="Schließen">×</button>`;
    container.appendChild(t);
    requestAnimationFrame(() => t.classList.add('show'));
    const close = () => {
      t.classList.remove('show');
      setTimeout(() => t.remove(), 250);
    };
    t.querySelector('.toast-close').addEventListener('click', close);
    if (duration > 0) setTimeout(close, duration);
  };

  // ====================================================================
  // FEATURE T: WhatsApp / Native-Share-Button
  // ====================================================================
  document.querySelectorAll('.share-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const text = btn.dataset.shareText || 'Wulmstörper Tipprunde';
      const url = window.location.href;
      // Native Share API (Mobile, manche Desktop)
      if (navigator.share) {
        try {
          await navigator.share({ title: 'Wulmstörper Tipprunde', text, url });
          return;
        } catch (e) {
          // User hat Share-Dialog abgebrochen → Fallback
          if (e.name === 'AbortError') return;
        }
      }
      // Fallback: WhatsApp-Web/-App-Link
      const fullText = `${text}\n${url}`;
      const waUrl = `https://wa.me/?text=${encodeURIComponent(fullText)}`;
      window.open(waUrl, '_blank', 'noopener,noreferrer');
    });
  });

  // ====================================================================
  // FEATURE L: Pull-to-Refresh auf Mobile
  // ====================================================================
  (function pullToRefresh() {
    const indicator = document.getElementById('ptrIndicator');
    if (!indicator) return;
    // Nur bei Touch-Geräten
    if (!('ontouchstart' in window)) return;

    let startY = 0, currentY = 0, pulling = false;
    const THRESHOLD = 80;

    document.addEventListener('touchstart', (e) => {
      if (window.scrollY > 0) return;
      startY = e.touches[0].clientY;
      pulling = true;
    }, { passive: true });

    document.addEventListener('touchmove', (e) => {
      if (!pulling) return;
      currentY = e.touches[0].clientY;
      const dy = currentY - startY;
      if (dy > 0 && window.scrollY === 0) {
        const pct = Math.min(dy / THRESHOLD, 1.5);
        indicator.style.transform = `translateY(${Math.min(dy * 0.5, 80)}px)`;
        indicator.style.opacity = pct;
        indicator.classList.toggle('ready', dy >= THRESHOLD);
      }
    }, { passive: true });

    document.addEventListener('touchend', () => {
      if (!pulling) return;
      const dy = currentY - startY;
      if (dy >= THRESHOLD) {
        indicator.classList.add('refreshing');
        indicator.style.transform = 'translateY(60px)';
        setTimeout(() => location.reload(), 200);
      } else {
        indicator.style.transform = '';
        indicator.style.opacity = '';
        indicator.classList.remove('ready');
      }
      pulling = false;
      startY = 0; currentY = 0;
    }, { passive: true });
  })();

  // ====================================================================
  // FEATURE Y: Smooth Page-Transitions
  // ====================================================================
  // Beim Navigieren zu interner URL: kurzer Fade-Out
  (function pageTransitions() {
    const main = document.querySelector('.page-transition');
    if (!main) return;

    function showPage() {
      main.classList.remove('pt-leave');
      main.classList.add('pt-enter');
    }

    requestAnimationFrame(showPage);

    // WICHTIG: Bei Browser-Back/Forward (bfcache) Seite wieder einblenden
    // Sonst bleibt 'pt-leave' aktiv → schwarzer Bildschirm!
    window.addEventListener('pageshow', showPage);
    window.addEventListener('popstate', showPage);
    // Visibility-Wechsel (Tab-Switch) auch zurücksetzen
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') showPage();
    });

    document.addEventListener('click', (e) => {
      const a = e.target.closest('a[href]');
      if (!a) return;
      const href = a.getAttribute('href');
      if (!href || href.startsWith('#') || href.startsWith('mailto:')
          || href.startsWith('tel:') || href.startsWith('https://wa.me')
          || a.hasAttribute('download') || a.target === '_blank'
          || e.ctrlKey || e.metaKey || e.shiftKey) return;
      try {
        const u = new URL(href, window.location.origin);
        if (u.origin !== window.location.origin) return;
      } catch (err) { return; }
      // Fallback-Timer: Falls Navigation aus irgendeinem Grund hängt,
      // Seite trotzdem wieder einblenden nach 2s
      main.classList.remove('pt-enter');
      main.classList.add('pt-leave');
      setTimeout(showPage, 2000);
    });
  })();

  // ====================================================================
  // FEATURE D: Optimistic UI für Tipp-Speichern (im Schnelltipp)
  // Wird in quick_tip.html erweitert - hier nur globale Helper
  // ====================================================================
  window.markTipSaved = function(matchId) {
    const row = document.querySelector(`.qt-row[data-match-id="${matchId}"]`);
    if (!row) return;
    row.classList.add('tip-saved-flash');
    setTimeout(() => row.classList.remove('tip-saved-flash'), 1200);
  };
});
