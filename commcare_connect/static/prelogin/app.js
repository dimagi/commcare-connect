// History-API router (clean URLs, no #).
//
// The SPA ships under several base paths and we can't hardcode any of them:
//   • GitHub Pages project root  → /connect-prelogin/
//   • GitHub Pages PR previews   → /connect-prelogin/preview/pr-<N>/
//   • Django apps (production)   → / (served at site root)
// So the mount base is detected at runtime and clean routes like /platform
// resolve identically everywhere. Direct links / refreshes on a sub-route are
// served the SPA by 404.html (GitHub Pages) or a catch-all route (Django) —
// see IMPORT.md. Those entry points hand the route back via ?p=… / a legacy #.
//
// Legacy slug aliases — old #/hash bookmarks and inbound links keep working
// after the hash-router → clean-URL rename. Resolved by resolveLegacy() below,
// which also rewrites the /programs/* → /portfolio/* prefix so every program
// detail page (and any added later) redirects without being listed here.
const LEGACY_ROUTES = {
  '/how-it-works': '/platform',
  '/the-why': '/the-opportunity',
  '/join': '/frontline-network',
  '/product-updates': '/release-notes',
  '/programs': '/portfolio',
  '/blog': '/insights', // the blog listing was merged into the Insights page
};

// Map a legacy path/slug to its current route. Exact aliases win; otherwise the
// /programs/<slug> → /portfolio/<slug> rule covers every program detail page.
// Returns the input unchanged when nothing matches.
function resolveLegacy(path) {
  if (LEGACY_ROUTES[path]) return LEGACY_ROUTES[path];
  if (path.indexOf('/programs/') === 0) {
    return '/portfolio/' + path.slice('/programs/'.length);
  }
  return path;
}

let ROUTES = []; // every data-page value present in the document
let APP_BASE = ''; // path prefix before the route, '' when served at site root
let currentRoute = '/'; // the route actually shown right now. The URL is NOT a
// reliable source of truth: on file:// history.pushState
// throws (opaque origin), so location.pathname never
// changes as you navigate. Track the shown route here so
// the click handler's "already on this page?" check works.

// file:// extras: the History API is blocked there (pushState/replaceState throw
// on the opaque origin), so the URL can't carry the route across a refresh. To
// keep a refresh on the same page we stash the route in sessionStorage and
// restore it on load. Both guards below are no-ops over http(s) — there the URL
// is the source of truth — so the deployed site is completely unaffected.
const FILE_PROTOCOL = location.protocol === 'file:';
const ROUTE_STORAGE_KEY = 'connect-prelogin:lastRoute';

function collectRoutes() {
  return Array.from(
    document.querySelectorAll('.page'),
    (el) => el.dataset.page,
  ).filter(Boolean);
}

// Strip the longest known route that is a suffix of the current path; whatever
// precedes it is the base. No route suffix → we're at the app root, so the
// whole path (minus a trailing slash) is the base.
function computeBase() {
  const p = location.pathname.replace(/\/index\.html$/, '/');
  // Legacy paths (e.g. /blog) are full routes too, not deployment subpaths —
  // include them so a direct load of one resolves to base "" and redirects.
  const candidates = ROUTES.concat(Object.keys(LEGACY_ROUTES))
    .filter((r) => r !== '/')
    .sort((a, b) => b.length - a.length);
  for (const r of candidates) {
    if (p.length >= r.length && p.slice(p.length - r.length) === r) {
      return p.slice(0, p.length - r.length);
    }
  }
  return p.replace(/\/$/, '');
}

function absUrl(route) {
  if (!route || route === '/') return (APP_BASE || '') + '/';
  return (APP_BASE || '') + route;
}

// Map any full pathname to a known app route, or null if it isn't one
// (e.g. the standalone /contact/ page, or an asset path).
function routeFromPath(pathname) {
  let p = pathname.replace(/\/index\.html$/, '/');
  if (APP_BASE && p.indexOf(APP_BASE) === 0) p = p.slice(APP_BASE.length);
  if (p === '') p = '/';
  if (p.length > 1) p = p.replace(/\/$/, '');
  if (p === '') p = '/';
  p = resolveLegacy(p);
  return ROUTES.indexOf(p) !== -1 ? p : null;
}

// Per-route <title> + meta description. The table itself is rendered by Django
// into <script type="application/json" id="route-meta"> from
// prelogin/route_meta.py, which is also what builds the server-side <head>.
// Reading it from there rather than keeping a second copy here is what stops
// the two from drifting: a page added on the server is immediately known to the
// client router. Unknown routes fall back to the home entry.
const SITE_ORIGIN = 'https://connect.dimagi.com';
const ROUTE_META = (function () {
  const node = document.getElementById('route-meta');
  if (!node) return {};
  try {
    return JSON.parse(node.textContent);
  } catch (e) {
    return {};
  }
})();

// Only the SPA document (index.html) carries the home route; the standalone
// contact/404 pages also load this script but must keep their own <title>.
const IS_SPA_DOC = !!document.querySelector('.page[data-page="/"]');

function setMetaAttr(selector, attr, value) {
  const el = document.querySelector(selector);
  if (el) el.setAttribute(attr, value);
}

function applyRouteMeta(route) {
  if (!IS_SPA_DOC) return;
  const m = ROUTE_META[route] || ROUTE_META['/'];
  // The server already rendered the correct head for the landing URL; if the
  // table is missing for any reason, leave it alone rather than blanking it.
  if (!m) return;
  document.title = m.title;
  setMetaAttr('meta[name="description"]', 'content', m.desc);
  setMetaAttr('meta[property="og:title"]', 'content', m.title);
  setMetaAttr('meta[property="og:description"]', 'content', m.desc);
  setMetaAttr('meta[name="twitter:title"]', 'content', m.title);
  setMetaAttr('meta[name="twitter:description"]', 'content', m.desc);
  // og:image was the one tag this never touched, so a post shared after an
  // in-page navigation still carried the home page's picture.
  if (m.image) {
    setMetaAttr('meta[property="og:image"]', 'content', m.image);
    setMetaAttr('meta[name="twitter:image"]', 'content', m.image);
    setMetaAttr('meta[property="og:image:alt"]', 'content', m.imageAlt || '');
  }
  setMetaAttr(
    'meta[property="og:type"]',
    'content',
    route.indexOf('/blog/') === 0 ? 'article' : 'website',
  );
  const url = SITE_ORIGIN + (route === '/' ? '/' : route);
  setMetaAttr('meta[property="og:url"]', 'content', url);
  setMetaAttr('link[rel="canonical"]', 'href', url);
}

function render(path, search) {
  const pages = document.querySelectorAll('.page');
  let matched = false;
  pages.forEach((p) => {
    const isMatch = p.dataset.page === path;
    p.classList.toggle('active', isMatch);
    if (isMatch) matched = true;
  });
  if (!matched) {
    const home = document.querySelector('.page[data-page="/"]');
    if (home) home.classList.add('active');
    // Standalone page (no home route on the document): show its single page.
    else if (pages[0]) pages[0].classList.add('active');
  }
  currentRoute = matched ? path : '/'; // remember what's actually on screen
  applyRouteMeta(currentRoute); // keep <title>/SEO/social meta in sync
  if (FILE_PROTOCOL) {
    // file://: no URL to refresh into — stash it
    try {
      sessionStorage.setItem(ROUTE_STORAGE_KEY, currentRoute);
    } catch (_) {}
  }
  document
    .querySelectorAll('#primary-nav a[data-route], .mobile-nav a[data-route]')
    .forEach((a) => {
      a.classList.toggle('active', a.dataset.route === path);
    });
  // Insights deep links: /insights?program= / ?activity= pre-apply the filter.
  if (currentRoute === '/insights' && window.__applyInsightsDeepLink) {
    window.__applyInsightsDeepLink(
      typeof search === 'string' ? search : location.search,
    );
  }
  window.scrollTo({
    top: 0,
    behavior: 'instant' in window ? 'instant' : 'auto',
  });
}

function navigate(route, search) {
  const q = search || '';
  try {
    history.pushState({ route }, '', absUrl(route) + q);
  } catch (_) {
    /* file://: section still switches, URL stays */
  }
  render(route, q);
}

// Rewrite authored clean hrefs (/platform) to include the runtime base
// (/connect-prelogin/platform), and tag each with its route so the click
// interceptor and active-state logic work base-independently.
function hydrateLinks() {
  document.querySelectorAll('a[href]').forEach((a) => {
    const raw = a.getAttribute('href');
    if (!raw || raw.charAt(0) !== '/') return; // in-page (#), relative, or external
    const q = raw.indexOf('?'); // keep any query (e.g. insights filters)
    const path = q === -1 ? raw : raw.slice(0, q);
    const search = q === -1 ? '' : raw.slice(q);
    const route = routeFromPath(path);
    if (route === null) return;
    a.dataset.route = route;
    a.setAttribute('href', absUrl(route) + search);
  });
}

// Pin relative <img> srcs to an absolute, base-aware path. A lazy/below-the-fold
// image authored as a relative path (e.g. images/…) re-resolves against the URL
// it sees when it finally fetches — and on a deep route like /portfolio/<slug>
// that lands under /portfolio/images/… and 404s (blank image). Anchoring to the
// app base up front (APP_BASE + '/' + path) makes the src route-independent.
function hydrateImages() {
  const base = APP_BASE || '';
  document.querySelectorAll('img[src]').forEach((img) => {
    const raw = img.getAttribute('src');
    // Skip root-relative (/…), protocol (http:, data:) and empty srcs.
    if (!raw || raw.charAt(0) === '/' || /^[a-z][a-z0-9+.-]*:/i.test(raw))
      return;
    img.setAttribute('src', base + '/' + raw);
  });
}

// Intercept same-origin clicks that target a known route → push + render.
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[href]');
  if (!a) return;
  const raw = a.getAttribute('href');
  if (!raw || raw.charAt(0) === '#') return; // in-page anchor / <use href>
  if (a.target && a.target !== '_self') return; // opens a new context
  if (a.hasAttribute('download')) return;
  if (
    e.defaultPrevented ||
    e.button !== 0 ||
    e.metaKey ||
    e.ctrlKey ||
    e.shiftKey ||
    e.altKey
  )
    return;
  let url;
  try {
    url = new URL(a.href, location.href);
  } catch (_) {
    return;
  }
  if (url.origin !== location.origin) return; // external
  const route = routeFromPath(url.pathname);
  if (route === null) return; // standalone page / asset → let it navigate
  e.preventDefault();
  const search = route === '/insights' ? url.search : ''; // only insights carries filter params
  if (route === currentRoute) {
    // already here
    if (search) {
      // …but a new filter → re-apply in place
      try {
        history.replaceState({ route }, '', absUrl(route) + search);
      } catch (_) {}
      render(route, search);
    } else {
      // otherwise just scroll up
      window.scrollTo({
        top: 0,
        behavior: 'instant' in window ? 'instant' : 'auto',
      });
    }
    return;
  }
  navigate(route, search);
});

window.addEventListener('popstate', () => {
  render(routeFromPath(location.pathname) || '/', location.search);
});

function initRouter() {
  ROUTES = collectRoutes();
  APP_BASE = computeBase();

  // Entry-point hand-offs resolve a start route and restore the clean URL
  // before the first render. The resolved route is tracked explicitly because
  // on file:// history.replaceState throws — so location.pathname can't be
  // trusted to reflect the redirect; render what we resolved, not the URL.
  let entry = null;
  const handoff = new URLSearchParams(location.search).get('p'); // 404.html fallback
  if (handoff) {
    const r = resolveLegacy(handoff);
    entry = ROUTES.indexOf(r) !== -1 ? r : '/';
    // Keep any non-handoff params (e.g. ?program / ?activity) on the clean URL so
    // the deep-linked insights filters survive the redirect.
    const kept = new URLSearchParams(location.search);
    kept.delete('p');
    const keptQuery = kept.toString();
    try {
      history.replaceState(
        null,
        '',
        absUrl(entry) + (keptQuery ? '?' + keptQuery : '') + location.hash,
      );
    } catch (_) {}
  } else if (/^#\//.test(location.hash)) {
    // legacy #/route bookmarks
    let r0 = location.hash.slice(1).split('?')[0];
    if (r0.length > 1) r0 = r0.replace(/\/$/, ''); // normalize trailing slash
    const r = resolveLegacy(r0);
    if (ROUTES.indexOf(r) !== -1) {
      entry = r;
      try {
        history.replaceState(null, '', absUrl(r));
      } catch (_) {}
    }
  }

  // file:// has no usable URL path (it's always the index.html file), so when no
  // explicit hand-off resolved, restore the route stashed before the last refresh.
  // http(s) skips this — there entry/routeFromPath already reflect the real URL.
  if (entry === null && FILE_PROTOCOL) {
    try {
      const saved = sessionStorage.getItem(ROUTE_STORAGE_KEY);
      if (saved && ROUTES.indexOf(saved) !== -1) entry = saved;
    } catch (_) {}
  }

  hydrateLinks();
  hydrateImages();
  render(entry || routeFromPath(location.pathname) || '/', location.search);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initRouter);
} else {
  initRouter();
}

// Mobile hamburger menu (visible at <=980px viewport - see media query in styles.css)
function setupNavToggle() {
  const btn = document.getElementById('nav-toggle');
  const panel = document.getElementById('mobile-nav');
  if (!btn || !panel) return;

  // Backdrop: dims the page behind the open menu; click closes it
  const backdrop = document.createElement('div');
  backdrop.className = 'nav-backdrop';
  backdrop.setAttribute('aria-hidden', 'true');
  document.body.insertBefore(backdrop, document.body.firstChild);

  // iOS Safari ignores overflow:hidden on <body>; position:fixed is the reliable fix
  let savedScrollY = 0;

  const close = (returnFocus = true) => {
    document.body.classList.remove('nav-open');
    document.body.style.position = '';
    document.body.style.top = '';
    document.body.style.width = '';
    window.scrollTo(0, savedScrollY);
    panel.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    btn.setAttribute('aria-label', 'Open menu');
    if (returnFocus) btn.focus({ preventScroll: true });
  };
  const open = () => {
    savedScrollY = window.scrollY;
    document.body.style.position = 'fixed';
    document.body.style.top = `-${savedScrollY}px`;
    document.body.style.width = '100%';
    document.body.classList.add('nav-open');
    panel.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    btn.setAttribute('aria-label', 'Close menu');
    const firstLink = panel.querySelector('a');
    if (firstLink) firstLink.focus({ preventScroll: true });
  };

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (document.body.classList.contains('nav-open')) close();
    else open();
  });
  // Tap a link in the panel -> close before navigation
  panel
    .querySelectorAll('a')
    .forEach((a) => a.addEventListener('click', close));
  // Tap outside the header (or on the backdrop) -> close without stealing focus
  document.addEventListener('click', (e) => {
    if (!document.body.classList.contains('nav-open')) return;
    if (!e.target.closest('.site-header')) close(false);
  });
  // Escape -> close, return focus to toggle button
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.classList.contains('nav-open'))
      close();
  });
  // Focus trap: keep Tab/Shift+Tab within the panel links while menu is open
  panel.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const focusable = Array.from(panel.querySelectorAll('a'));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });
  // Closing on popstate catches browser back/forward across routes (link taps
  // close the panel via the per-link click handler above).
  window.addEventListener('popstate', () => close(false));
}
window.addEventListener('DOMContentLoaded', setupNavToggle);

// (The legacy Insights pill-filter code lived here. The Insights page now
// filters its evidence rows through the dropdown bar in insightsFilters(),
// so the old pill handler + applyInsightFilters/*FromQuery were removed.)

// Picker lists: click selects, auto-cycles every 5s
document.addEventListener('click', (e) => {
  const item = e.target.closest('.picker-scroll .picker-item');
  if (!item) return;
  const scroll = item.parentElement;
  scroll
    .querySelectorAll('.picker-item')
    .forEach((i) => i.classList.remove('is-active'));
  item.classList.add('is-active');
  scrollPickerToActive(scroll);
  // Pause auto-cycle for a moment after manual click
  scroll.dataset.paused = String(Date.now() + 6000);
});

function scrollPickerToActive(scroll) {
  const active = scroll.querySelector('.picker-item.is-active');
  if (!active) return;
  const targetTop =
    active.offsetTop - scroll.clientHeight / 2 + active.offsetHeight / 2;
  scroll.scrollTo({ top: targetTop, behavior: 'smooth' });
}

function cyclePickers() {
  document
    .querySelectorAll('.picker-scroll[data-cycle="1"]')
    .forEach((scroll) => {
      // Skip if recently clicked
      const paused = parseInt(scroll.dataset.paused || '0', 10);
      if (paused && Date.now() < paused) return;
      const items = Array.from(scroll.querySelectorAll('.picker-item'));
      if (items.length <= 1) return;
      const activeIdx = items.findIndex((i) =>
        i.classList.contains('is-active'),
      );
      const nextIdx = (activeIdx + 1) % items.length;
      items.forEach((item, idx) =>
        item.classList.toggle('is-active', idx === nextIdx),
      );
      scrollPickerToActive(scroll);
    });
}
// Don't auto-advance the pickers for users who prefer reduced motion.
if (
  !window.matchMedia ||
  !matchMedia('(prefers-reduced-motion: reduce)').matches
) {
  setInterval(cyclePickers, 5000);
}

// In-page scroll anchor: data-scroll-to="<element-id>" avoids router conflict
document.addEventListener('click', (e) => {
  const link = e.target.closest('[data-scroll-to]');
  if (!link) return;
  e.preventDefault();
  const target = document.getElementById(link.dataset.scrollTo);
  if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
});

// "Learn more" hero buttons (program detail pages): smooth-scroll to the
// section right after the hero, so no per-page anchor id is required.
document.addEventListener('click', (e) => {
  const trigger = e.target.closest('[data-scroll-next]');
  if (!trigger) return;
  e.preventDefault();
  const hero = trigger.closest('.hero-dark');
  const target = hero && hero.nextElementSibling;
  if (!target) return;
  const reduce =
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  target.scrollIntoView({
    behavior: reduce ? 'auto' : 'smooth',
    block: 'start',
  });
});

// Service tabs (CHC "Inside a Campaign")
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.service-tab-btn');
  if (!btn) return;
  const tabs = btn.closest('.service-tabs');
  const idx = btn.dataset.stab;
  tabs.querySelectorAll('.service-tab-btn').forEach((b) => {
    b.classList.toggle('is-active', b === btn);
    b.setAttribute('aria-selected', String(b === btn));
  });
  tabs.querySelectorAll('.service-tab-panel').forEach((p) => {
    p.classList.toggle('is-active', p.dataset.stab === idx);
  });
});

// Mobile-only toggle on the "What's different" comparison table. CSS hides
// the unselected column at the same breakpoint where the table goes 1fr.
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.compare-toggle-btn');
  if (!btn) return;
  const show = btn.dataset.show;
  const toggle = btn.closest('.compare-toggle');
  const tableId = toggle && toggle.getAttribute('aria-controls');
  const table = tableId && document.getElementById(tableId);
  if (!show || !table) return;
  table.setAttribute('data-mobile-show', show);
  toggle.querySelectorAll('.compare-toggle-btn').forEach((b) => {
    const active = b === btn;
    b.classList.toggle('is-active', active);
    b.setAttribute('aria-selected', String(active));
  });
});

// Compare table: row hover highlight + scroll-reveal stagger (works for all .compare-table instances)
(function initCompareTables() {
  const COLS = 3;
  const animated = window.matchMedia(
    '(prefers-reduced-motion: no-preference)',
  ).matches;

  document.querySelectorAll('.compare-table[id]').forEach((table) => {
    const cells = table.querySelectorAll('.compare-cell');

    // Row hover: highlight all 3 cells in the same logical row together
    cells.forEach((cell, i) => {
      const rowStart = Math.floor(i / COLS) * COLS;
      const rowCells = Array.from(
        { length: COLS },
        (_, c) => cells[rowStart + c],
      ).filter(Boolean);
      cell.addEventListener('mouseenter', () =>
        rowCells.forEach((c) => c.classList.add('row-hover')),
      );
      cell.addEventListener('mouseleave', () =>
        rowCells.forEach((c) => c.classList.remove('row-hover')),
      );
    });

    // Scroll-reveal: stagger rows in one by one when the table enters the viewport
    if (!animated) return;
    table.classList.add('compare-animate');
    const rowCount = Math.ceil(cells.length / COLS);
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          for (let row = 0; row < rowCount; row++) {
            setTimeout(() => {
              for (let col = 0; col < COLS; col++) {
                const c = cells[row * COLS + col];
                if (c) c.classList.add('is-visible');
              }
            }, row * 70);
          }
          observer.unobserve(entry.target);
        });
      },
      { threshold: 0.05 },
    );
    observer.observe(table);
  });
})();

// Where We Work map: SVG is inlined in the HTML, so wire it up directly.
// Tooltip card follows the cursor with per-country program + MOU data.
(function initWhereMap() {
  /* <<< map-country-data: generated from data/programs.json by tools/sync-map-data.py — do not edit by hand >>> */
  const COUNTRY_DATA = {
    CAF: {
      name: 'Central African Republic',
      programs: ['Child Health Campaign'],
    },
    COD: {
      name: 'DR Congo',
      programs: ['Child Health Campaign'],
      mou: '14 Provincial Governments',
    },
    ETH: {
      name: 'Ethiopia',
      programs: ['Kangaroo Mother Care', 'Group Therapy for Depression'],
    },
    IND: { name: 'India', programs: ['Kangaroo Mother Care'] },
    KEN: {
      name: 'Kenya',
      programs: [
        'Child Health Campaign',
        'Kangaroo Mother Care',
        'Reading Glasses',
      ],
      mou: 'Turkana County',
    },
    LBR: {
      name: 'Liberia',
      programs: ['Child Health Campaign'],
      mou: 'Ministry of Health',
    },
    MOZ: { name: 'Mozambique', programs: ['Early Childhood Development'] },
    MWI: { name: 'Malawi', programs: ['Early Childhood Development'] },
    NGA: {
      name: 'Nigeria',
      programs: [
        'Child Health Campaign',
        'Kangaroo Mother Care',
        'Early Childhood Development',
        'Mother Baby Wellness',
        'Chlorine Dispenser',
        'Connect Interview',
        'Therapeutic Food',
        'Rooftop Sampling',
      ],
    },
    SLE: {
      name: 'Sierra Leone',
      programs: ['Child Health Campaign'],
      mou: 'Ministry of Health',
    },
    TZA: { name: 'Tanzania', programs: ['Child Health Campaign'] },
    UGA: {
      name: 'Uganda',
      programs: [
        'Child Health Campaign',
        'Kangaroo Mother Care',
        'Group Therapy for Depression',
      ],
      mou: 'Uganda Ministry of Health',
    },
    ZMB: { name: 'Zambia', programs: ['Child Health Campaign'] },
  };
  /* <<< end map-country-data >>> */

  function load() {
    const host = document.getElementById('where-we-work-map');
    if (!host || host.dataset.loaded) return;
    host.dataset.loaded = '1';
    // SVG is already inlined — wire it up immediately
    const svg = host.querySelector('svg');
    if (svg) {
      svg.setAttribute('role', 'img');
      wire(host);
      return;
    }
    // Fallback: fetch from disk (only works on HTTP servers, not file://)
    const src = host.dataset.svg;
    if (!src) return;
    const loading = document.createElement('div');
    loading.className = 'where-map-loading';
    loading.textContent = 'Loading map…';
    host.appendChild(loading);
    fetch(src)
      .then((r) =>
        r.ok ? r.text() : Promise.reject(new Error('http ' + r.status)),
      )
      .then((txt) => {
        loading.remove();
        host.insertAdjacentHTML('afterbegin', txt);
        const svgEl = host.querySelector('svg');
        if (svgEl) svgEl.setAttribute('role', 'img');
        wire(host);
      })
      .catch(() => {
        loading.textContent = 'Map could not be loaded.';
      });
  }

  function buildCard(host) {
    const card = document.createElement('div');
    card.className = 'where-card';
    card.setAttribute('role', 'tooltip');
    card.innerHTML =
      '<div class="where-card-head"><span class="where-card-name"></span></div>' +
      '<ul class="where-card-programs"></ul>' +
      '<p class="where-card-note"></p>' +
      '<div class="where-card-mou"></div>';
    host.appendChild(card);
    return card;
  }

  function positionCard(card, host, mouseX, mouseY) {
    const OFFSET = 16;
    const PAD = 10;
    const HW = host.offsetWidth;
    const HH = host.offsetHeight;
    const CW = card.offsetWidth || 240;
    const CH = card.offsetHeight || 130;

    let x = mouseX + OFFSET;
    let y = mouseY + OFFSET;

    if (x + CW + PAD > HW) x = mouseX - CW - OFFSET;
    if (y + CH + PAD > HH) y = mouseY - CH - OFFSET;

    x = Math.max(PAD, Math.min(x, HW - CW - PAD));
    y = Math.max(PAD, Math.min(y, HH - CH - PAD));

    card.style.left = x + 'px';
    card.style.top = y + 'px';
  }

  function wire(host) {
    const card = buildCard(host);
    const nameEl = card.querySelector('.where-card-name');
    const programsEl = card.querySelector('.where-card-programs');
    const noteEl = card.querySelector('.where-card-note');
    const mouEl = card.querySelector('.where-card-mou');

    let mouseX = 0;
    let mouseY = 0;
    let hideTimer = null;

    host.addEventListener('mousemove', (e) => {
      const rect = host.getBoundingClientRect();
      mouseX = e.clientX - rect.left;
      mouseY = e.clientY - rect.top;
      if (card.classList.contains('is-visible')) {
        positionCard(card, host, mouseX, mouseY);
      }
    });

    const paths = host.querySelectorAll('svg .hl[data-country]');
    paths.forEach((p) => {
      const code = p.getAttribute('data-country');
      const data = COUNTRY_DATA[code] || { name: code, programs: [] };

      if (!p.querySelector('title')) {
        const t = document.createElementNS(
          'http://www.w3.org/2000/svg',
          'title',
        );
        t.textContent = data.name;
        p.appendChild(t);
      }
      p.setAttribute('tabindex', '0');
      p.setAttribute('role', 'button');
      p.setAttribute('aria-label', data.name);

      const enter = () => {
        clearTimeout(hideTimer);
        host.classList.add('is-hovering');

        nameEl.textContent = data.name;
        programsEl.innerHTML = data.programs
          .map((pr) => '<li>' + pr + '</li>')
          .join('');

        if (data.note) {
          noteEl.textContent = data.note;
          noteEl.style.display = '';
        } else {
          noteEl.style.display = 'none';
        }

        if (data.mou) {
          mouEl.textContent = 'MOU · ' + data.mou;
          mouEl.style.display = '';
        } else {
          mouEl.style.display = 'none';
        }

        positionCard(card, host, mouseX, mouseY);
        card.classList.add('is-visible');
      };

      const leave = () => {
        hideTimer = setTimeout(() => {
          host.classList.remove('is-hovering');
          card.classList.remove('is-visible');
        }, 80);
      };

      p.addEventListener('mouseenter', enter);
      p.addEventListener('mouseleave', leave);
      p.addEventListener('focus', enter);
      p.addEventListener('blur', leave);
    });
  }

  if (document.readyState !== 'loading') load();
  else document.addEventListener('DOMContentLoaded', load);
})();

// Connect Model stepper — Step 2 inner tabs only (Learn / Deliver / Verify / Pay)
(function () {
  function init() {
    var flwTabs = Array.from(document.querySelectorAll('.mf-flw-tab'));
    var flwPanels = Array.from(document.querySelectorAll('.mf-flw-panel'));

    if (!flwTabs.length) return;

    function activateFlw(idx) {
      flwTabs.forEach(function (t, i) {
        t.setAttribute('aria-selected', i === idx ? 'true' : 'false');
      });
      flwPanels.forEach(function (p, i) {
        if (i === idx) p.removeAttribute('hidden');
        else p.setAttribute('hidden', '');
      });
    }

    // No auto-advance: the user clicks through the steps themselves.
    // The next tab's icon sits a touch brighter than the other inactive icons
    // (static cue in styles.css) to hint which step to click next.
    flwTabs.forEach(function (tab, i) {
      tab.addEventListener('click', function () {
        activateFlw(i);
      });
    });

    activateFlw(0);
  }

  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();

// Product Updates — sidebar tab switching
(function productUpdatesTabs() {
  function init() {
    const section = document.querySelector('[data-page="/release-notes"]');
    if (!section) return;
    const tabs = section.querySelectorAll('.cl-tab');
    const panels = section.querySelectorAll('.cl-panel');
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        tabs.forEach((t) => t.classList.remove('active'));
        panels.forEach((p) => p.classList.remove('active'));
        tab.classList.add('active');
        const target = section.querySelector('#' + tab.dataset.panel);
        if (target) target.classList.add('active');
      });
    });
  }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();

// Sync footer tagline to match the hero headline
(function syncFooterTagline() {
  function sync() {
    const headline = document.getElementById('hero-headline');
    const tagline = document.getElementById('footer-tagline');
    if (headline && tagline) tagline.innerHTML = headline.innerHTML;
  }
  if (document.readyState !== 'loading') sync();
  else document.addEventListener('DOMContentLoaded', sync);
})();

// Testimonial marquee — the row scrolls continuously, pausing on hover / focus.
(function testimonialMarquee() {
  function setup(marquee) {
    const track = marquee.querySelector('.testimonial-cards');
    if (!track || !track.children.length) return;

    // Respect reduced-motion: leave a static, manually scrollable row.
    const reduce =
      window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce) {
      track.classList.add('testimonial-cards--static');
      return;
    }

    // Duplicate the set once so the row can loop seamlessly.
    const originalCount = track.children.length;
    Array.prototype.slice.call(track.children).forEach(function (card) {
      const clone = card.cloneNode(true);
      clone.setAttribute('aria-hidden', 'true');
      clone.querySelectorAll('a, button, [tabindex]').forEach(function (el) {
        el.tabIndex = -1;
      });
      track.appendChild(clone);
    });

    // One full loop = the distance from the first original card to its clone.
    let cycle = 0;
    const measure = function () {
      const first = track.children[0];
      const firstClone = track.children[originalCount];
      cycle = firstClone ? firstClone.offsetLeft - first.offsetLeft : 0;
    };
    measure();
    window.addEventListener('resize', measure);
    if (window.ResizeObserver) new ResizeObserver(measure).observe(track);

    const SPEED = 42; // px per second at full speed
    const EASE_RATE = 7; // how quickly speed glides toward its target (~0.4s settle)
    let offset = 0;
    let last = null;
    let speed = 1; // current, eased speed multiplier
    let target = 1; // where speed is headed: 1 = scrolling, 0 = paused

    const frame = function (now) {
      if (last === null) last = now;
      const dt = (now - last) / 1000;
      last = now;
      // Glide speed toward its target so hover eases the row to a stop and
      // back up to speed, instead of snapping to a dead stop.
      speed += (target - speed) * (1 - Math.exp(-dt * EASE_RATE));
      // The section is display:none until its SPA page is shown, so the
      // first measure can land at 0 — re-measure until the row has width.
      if (cycle <= 0) measure();
      if (cycle > 0 && speed > 0.0005) {
        offset = (offset + SPEED * speed * dt) % cycle;
        track.style.transform = 'translateX(' + -offset + 'px)';
      }
      window.requestAnimationFrame(frame);
    };

    const pause = function () {
      target = 0;
    };
    const resume = function () {
      target = 1;
    };
    marquee.addEventListener('mouseenter', pause);
    marquee.addEventListener('mouseleave', resume);
    marquee.addEventListener('focusin', pause);
    marquee.addEventListener('focusout', resume);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        target = 0;
        speed = 0;
      } else {
        target = 1;
        last = null;
      }
    });

    window.requestAnimationFrame(frame);
  }
  function init() {
    document.querySelectorAll('.testimonial-carousel').forEach(setup);
  }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();

// Support KMC (/support-kmc): donation frequency toggle swaps each
// donate-button's href between its one-time / monthly / quarterly / yearly
// Stripe Payment Link, and the sticky "Donate Now" bar appears once the hero
// has scrolled out of view.
(function () {
  function setup(section) {
    const oneTimeBtn = section.querySelector('#oneTimeBtn');
    const recurringBtn = section.querySelector('#recurringBtn');
    const recurringOptions = section.querySelector('#recurringOptions');
    if (!oneTimeBtn || !recurringBtn || !recurringOptions) return;
    const donateButtons = section.querySelectorAll('.donate-button');

    function setFrequency(freq) {
      donateButtons.forEach((btn) => {
        const url = btn.dataset[freq === 'onetime' ? 'onetime' : freq];
        if (url) btn.href = url;
      });
    }

    function setPressed(btn, pressed) {
      btn.classList.toggle('active', pressed);
      btn.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    }

    oneTimeBtn.addEventListener('click', () => {
      setPressed(oneTimeBtn, true);
      setPressed(recurringBtn, false);
      recurringOptions.classList.remove('visible');
      setFrequency('onetime');
    });

    recurringBtn.addEventListener('click', () => {
      setPressed(recurringBtn, true);
      setPressed(oneTimeBtn, false);
      recurringOptions.classList.add('visible');
      const activeSub =
        recurringOptions.querySelector('button.active') ||
        recurringOptions.querySelector('button');
      recurringOptions
        .querySelectorAll('button')
        .forEach((b) => setPressed(b, false));
      setPressed(activeSub, true);
      setFrequency(activeSub.dataset.frequency);
    });

    recurringOptions.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => {
        recurringOptions
          .querySelectorAll('button')
          .forEach((b) => setPressed(b, false));
        setPressed(btn, true);
        setFrequency(btn.dataset.frequency);
      });
    });

    // Only float the sticky CTA while /support-kmc is the active SPA route —
    // the hero sits in the DOM (just display:none) on every other page too,
    // so an unguarded observer would flag it as "not intersecting" the
    // moment the visitor navigates away and incorrectly show the CTA there.
    const hero = section.querySelector('.hero-dark');
    const stickyCta = section.querySelector('#stickyCta');
    if (hero && stickyCta) {
      const setCtaVisible = (visible) => {
        stickyCta.classList.toggle('visible', visible);
        stickyCta.setAttribute('aria-hidden', visible ? 'false' : 'true');
        stickyCta.setAttribute('tabindex', visible ? '0' : '-1');
      };
      const observer = new IntersectionObserver(
        ([entry]) => {
          setCtaVisible(
            section.classList.contains('active') && !entry.isIntersecting,
          );
        },
        { threshold: 0 },
      );
      observer.observe(hero);
    }
  }
  function init() {
    document.querySelectorAll('[data-page="/support-kmc"]').forEach(setup);
  }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();

// Insights page: ONE filter bar (Search, Type, Program, Activity) over all
// content — blog/podcast cards and evidence data rows. Sections hide when they
// have no visible items. /insights?program=…/?activity=… pre-apply the filter.
(function insightsFilters() {
  function init() {
    const page = document.querySelector('[data-page="/insights"]');
    if (!page) return;
    const band = page.querySelector('.blog-filter-band');
    if (!band) return;
    const dds = Array.from(band.querySelectorAll('.blog-dd'));
    const searchInput = band.querySelector('.blog-search-input');
    const countWrap = band.querySelector('.blog-result-count');
    const countEl = countWrap ? countWrap.firstElementChild : null;
    const clearBtn = band.querySelector('.blog-clear');
    const chipsWrap = band.querySelector('.active-chips');
    const emptyEl = page.querySelector('.blog-empty');
    const blogSection = page.querySelector('.blog-grid-section');
    const evidenceSection = page.querySelector('.insights-evidence');

    const cards = Array.from(page.querySelectorAll('.blog-card'));
    const rows = Array.from(page.querySelectorAll('.insight-row'));
    const items = cards.concat(rows);
    if (!dds.length || !items.length) return;

    // Date-sort the blog/podcast cards (newest first).
    if (cards.length) {
      const grid = cards[0].parentElement;
      const dateOf = (el) => {
        const t = el.querySelector('time[datetime]');
        return t ? t.getAttribute('datetime') : '';
      };
      cards
        .slice()
        .sort((a, b) => dateOf(b).localeCompare(dateOf(a)))
        .forEach((el) => grid.appendChild(el));
    }

    const isRow = (it) => it.classList.contains('insight-row');
    const asList = (s) => (s || '').split(/\s+/).filter(Boolean);
    // Per-item dimension values. Blog cards use data-type / data-program; the
    // evidence rows are type "evidence" and carry data-programs / data-ldvp.
    const DIMS = [
      {
        key: 'type',
        get: (it) => (isRow(it) ? ['evidence'] : asList(it.dataset.type)),
      },
      {
        key: 'program',
        get: (it) => asList(it.dataset.program || it.dataset.programs),
      },
      {
        key: 'activity',
        get: (it) => asList(it.dataset.activity || it.dataset.ldvp),
      },
    ];

    const searchText = new Map();
    items.forEach((it) => {
      const txt = isRow(it)
        ? it.textContent
        : ['h3', '.blog-card-excerpt', '.blog-tag']
            .map((s) => {
              const e = it.querySelector(s);
              return e ? e.textContent : '';
            })
            .join(' ');
      searchText.set(it, txt.toLowerCase());
    });

    const state = { q: '', qRaw: '' };
    DIMS.forEach((d) => {
      state[d.key] = 'all';
      state[d.key + 'Label'] = '';
    });

    function closeAllDropdowns(except) {
      dds.forEach((dd) => {
        if (dd === except) return;
        dd.classList.remove('is-open');
        const menu = dd.querySelector('.blog-dd-menu');
        const trig = dd.querySelector('.blog-dd-trigger');
        if (menu) menu.hidden = true;
        if (trig) trig.setAttribute('aria-expanded', 'false');
      });
    }

    function setDropdown(key, value, fallbackLabel) {
      const dd = dds.find((d) => d.dataset.dim === key);
      if (!dd) return;
      const options = Array.from(dd.querySelectorAll('.blog-dd-option'));
      const opt = options.find((o) => o.dataset.value === value);
      options.forEach((o) => {
        const on = o === opt;
        o.classList.toggle('is-active', on);
        o.setAttribute('aria-selected', String(on));
      });
      const label = opt ? opt.textContent.trim() : fallbackLabel || value;
      dd.querySelector('.blog-dd-value').textContent =
        value === 'all' || !opt ? 'All' : label;
      state[key] = value;
      state[key + 'Label'] = value === 'all' ? '' : label;
    }

    function renderChips() {
      const active = [];
      DIMS.forEach((d) => {
        if (state[d.key] !== 'all')
          active.push({ key: d.key, label: state[d.key + 'Label'] });
      });
      if (state.q) active.push({ key: 'q', label: '“' + state.qRaw + '”' });
      if (chipsWrap) {
        chipsWrap.innerHTML = '';
        active.forEach((a) => {
          const chip = document.createElement('span');
          chip.className = 'active-chip';
          chip.textContent = a.label;
          const x = document.createElement('button');
          x.type = 'button';
          x.className = 'active-chip-x';
          x.setAttribute('aria-label', 'Remove filter ' + a.label);
          x.textContent = '×';
          x.addEventListener('click', () => {
            if (a.key === 'q') {
              state.q = '';
              state.qRaw = '';
              if (searchInput) searchInput.value = '';
            } else {
              setDropdown(a.key, 'all');
            }
            apply();
          });
          chip.appendChild(x);
          chipsWrap.appendChild(chip);
        });
        chipsWrap.hidden = active.length === 0;
      }
      if (clearBtn) clearBtn.hidden = active.length === 0;
    }

    function apply() {
      let shown = 0;
      let cardsShown = 0;
      let rowsShown = 0;
      items.forEach((it) => {
        let ok = true;
        for (const d of DIMS) {
          if (state[d.key] === 'all') continue;
          if (!d.get(it).includes(state[d.key])) {
            ok = false;
            break;
          }
        }
        if (ok && state.q && !(searchText.get(it) || '').includes(state.q))
          ok = false;
        it.classList.toggle('is-hidden', !ok);
        if (ok) {
          shown++;
          if (isRow(it)) rowsShown++;
          else cardsShown++;
        }
      });
      if (countEl) countEl.textContent = String(shown);
      if (blogSection) blogSection.hidden = cardsShown === 0;
      if (evidenceSection) evidenceSection.hidden = rowsShown === 0;
      if (emptyEl) emptyEl.hidden = shown !== 0;
      renderChips();
    }

    function clearAll() {
      DIMS.forEach((d) => setDropdown(d.key, 'all'));
      state.q = '';
      state.qRaw = '';
      if (searchInput) searchInput.value = '';
      apply();
    }

    dds.forEach((dd) => {
      const key = dd.dataset.dim;
      const trig = dd.querySelector('.blog-dd-trigger');
      const menu = dd.querySelector('.blog-dd-menu');
      trig.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = !dd.classList.contains('is-open');
        closeAllDropdowns(dd);
        dd.classList.toggle('is-open', willOpen);
        menu.hidden = !willOpen;
        trig.setAttribute('aria-expanded', String(willOpen));
      });
      dd.querySelectorAll('.blog-dd-option').forEach((opt) => {
        opt.addEventListener('click', () => {
          setDropdown(key, opt.dataset.value);
          dd.classList.remove('is-open');
          menu.hidden = true;
          trig.setAttribute('aria-expanded', 'false');
          apply();
        });
      });
    });

    if (searchInput) {
      searchInput.addEventListener('input', () => {
        state.qRaw = searchInput.value.trim();
        state.q = state.qRaw.toLowerCase();
        apply();
      });
    }
    if (clearBtn) clearBtn.addEventListener('click', clearAll);
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.blog-dd')) closeAllDropdowns(null);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeAllDropdowns(null);
    });
    if (emptyEl) {
      const reset = emptyEl.querySelector('.blog-empty-reset');
      if (reset) reset.addEventListener('click', clearAll);
    }

    // Deep links from the program / platform pages.
    const PROGRAM_LABELS = {
      chc: 'Child Health Campaign',
      kmc: 'Kangaroo Mother Care',
      ecd: 'Early Childhood Development',
    };
    const ACTIVITY_LABELS = {
      learn: 'Learn',
      deliver: 'Deliver',
      verify: 'Verify',
      pay: 'Pay',
    };
    function applyDeepLink(search) {
      const p = new URLSearchParams(search || '');
      const program = p.get('program');
      const activity = p.get('activity');
      // Reset each time so a plain /insights is a clean, unfiltered view.
      DIMS.forEach((d) => setDropdown(d.key, 'all'));
      state.q = '';
      state.qRaw = '';
      if (searchInput) searchInput.value = '';
      if (program) setDropdown('program', program, PROGRAM_LABELS[program]);
      if (activity)
        setDropdown('activity', activity, ACTIVITY_LABELS[activity]);
      apply();
    }
    window.__applyInsightsDeepLink = applyDeepLink;
    applyDeepLink(location.search);

    // Cross-site cards (Connect-tagged podcasts pulled live from dimagi.com)
    // arrive after init. Fold them into the same cards/items/searchText the
    // filter already uses, re-sort by date, and re-apply the current filter so
    // they behave exactly like the server-rendered cards. Event listeners are
    // bound once, above, so this never double-binds them.
    window.__insightsAddCards = function (newCards) {
      if (!newCards || !newCards.length || !cards.length) return;
      const grid = cards[0].parentElement;
      newCards.forEach((el) => {
        grid.appendChild(el);
        cards.push(el);
        items.push(el);
        const txt = ['h3', '.blog-card-excerpt', '.blog-tag']
          .map((s) => {
            const e = el.querySelector(s);
            return e ? e.textContent : '';
          })
          .join(' ');
        searchText.set(el, txt.toLowerCase());
      });
      const dateOf = (el) => {
        const t = el.querySelector('time[datetime]');
        return t ? t.getAttribute('datetime') : '';
      };
      cards
        .slice()
        .sort((a, b) => dateOf(b).localeCompare(dateOf(a)))
        .forEach((el) => grid.appendChild(el));
      apply();
    };
  }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();

// Connect-tagged podcasts, pulled LIVE from dimagi.com on page load instead of
// a monthly cron that scraped the dimagi podcast page and opened a PR.
// dimagi.com publishes /podcast/connect-manifest.json (CORS-open to this
// origin); this fetches it, builds link-out cards, and hands them to the
// Insights filter via __insightsAddCards, so a newly Connect-tagged episode
// shows up here on the next page load with nothing to merge. If the fetch fails
// (offline, or the feed isn't deployed yet) the page just shows the blog cards.
(function loadConnectPodcasts() {
  const MANIFEST_URL = 'https://dimagi.com/podcast/connect-manifest.json';
  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : s;
    return d.innerHTML;
  }
  // Light touch-ups so dimagi's wording reads in Connect's voice.
  function clean(s) {
    return (s || '')
      .replace(/CommCare Connect/g, 'Connect')
      .replace(/\s*[—–]\s*/g, ', ');
  }
  function buildCard(ep) {
    const a = document.createElement('a');
    a.className = 'blog-card blog-card--podcast';
    a.href = ep.url;
    a.target = '_blank';
    a.rel = 'noopener';
    a.dataset.type = 'podcast';
    a.dataset.program = '';
    const tag = ep.episodeNumber
      ? 'Podcast · Episode ' + ep.episodeNumber
      : 'Podcast';
    a.innerHTML =
      '<div class="blog-card-media">' +
      '<img src="' +
      esc(ep.coverImage || '') +
      '" alt="High-Impact Growth podcast cover art" loading="lazy">' +
      '<span class="blog-card-play" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg></span>' +
      '</div>' +
      '<div class="blog-card-body">' +
      '<div class="blog-card-meta">' +
      '<span class="blog-tag">' +
      esc(tag) +
      '</span>' +
      '<time datetime="' +
      esc(ep.date || '') +
      '">' +
      esc((ep.dateLabel || '').toUpperCase()) +
      '</time>' +
      '</div>' +
      '<h3>' +
      esc(clean(ep.title)) +
      '</h3>' +
      '<p class="blog-card-excerpt">' +
      esc(clean(ep.description)) +
      '</p>' +
      '<span class="blog-card-more">Listen on the Dimagi podcast ↗</span>' +
      '</div>';
    return a;
  }
  function init() {
    const page = document.querySelector('[data-page="/insights"]');
    if (!page || !page.querySelector('.blog-grid')) return;
    fetch(MANIFEST_URL)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data || !data.episodes || !data.episodes.length) return;
        const cards = data.episodes.map(buildCard);
        if (window.__insightsAddCards) window.__insightsAddCards(cards);
      })
      .catch(() => {});
  }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();
