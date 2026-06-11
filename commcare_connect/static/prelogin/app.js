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
  const candidates = ROUTES.filter((r) => r !== '/').sort(
    (a, b) => b.length - a.length,
  );
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

// Per-route <title> + meta description. Each clean URL is served standalone by
// the Django catch-all / 404 fallback, so every route must carry its own title
// and description for search indexing and social sharing. Without this, all
// routes would inherit the home document's single <title>/<meta>. Unknown
// routes fall back to the home entry.
const SITE_ORIGIN = 'https://connect.dimagi.com';
const ROUTE_META = {
  '/': {
    title: 'Connect by Dimagi | Pay for verified service delivery',
    desc: 'Connect is a pay-for-results service delivery platform. Pick a program, country, and budget. Verified Frontline Workers deliver and get paid the moment each service is confirmed.',
  },
  '/the-opportunity': {
    title: 'The opportunity | Connect by Dimagi',
    desc: 'Funders can’t verify what reaches communities, and Frontline Workers deliver results no one tracks. Connect is the missing link between them.',
  },
  '/platform': {
    title: 'Platform | Connect by Dimagi',
    desc: 'Pick the program, geography, and amount. Connect activates local organizations, verifies every service, and pays only for what’s confirmed.',
  },
  '/portfolio': {
    title: 'Portfolio | Connect by Dimagi',
    desc: 'Explore the growing portfolio of high-impact health and development programs delivered and verified through Connect, with new programs and countries added all the time.',
  },
  '/frontline-network': {
    title: 'Frontline Network | Connect by Dimagi',
    desc: 'Hundreds of frontline organizations already deliver services and get paid for verified work through Connect. See why they join and run programs on the network.',
  },
  '/insights': {
    title: 'Insights | Connect by Dimagi',
    desc: 'What’s working, what isn’t, and how the Connect model keeps shifting. Research and learnings from frontline service delivery.',
  },
  '/release-notes': {
    title: 'Release notes | Connect by Dimagi',
    desc: 'Release notes for the Connect platform, published with each major update to the mobile app and web tools.',
  },
  '/portfolio/child-health-campaign': {
    title: 'Child Health Campaigns | Connect by Dimagi',
    desc: 'Door-to-door delivery of high-impact health services to every child under five, verified visit by visit and paid only for what’s confirmed.',
  },
  '/portfolio/kangaroo-mother-care': {
    title: 'Kangaroo Mother Care | Connect by Dimagi',
    desc: 'Structured home visits for small and vulnerable newborns in their first 60 days, verified and paid only when confirmed, closing the post-discharge gap.',
  },
  '/portfolio/early-childhood-development': {
    title: 'Early Childhood Development | Connect by Dimagi',
    desc: 'Home visits supporting responsive caregiving and early child development, building caregiver knowledge, observable teaching behavior, and child autonomy.',
  },
  '/portfolio/reading-glasses': {
    title: 'Reading Glasses | Connect by Dimagi',
    desc: 'Door-to-door near-vision screening and presbyopia correction across northeast Nigeria.',
  },
  '/portfolio/mother-baby-wellness': {
    title: 'Mother Baby Wellness | Connect by Dimagi',
    desc: 'Frontline coaches support families with breastfeeding support and maternal mental health care. Six structured home visits per family, paid on verified outcomes.',
  },
  '/portfolio/chlorine-dispenser': {
    title: 'Chlorine Dispenser | Connect by Dimagi',
    desc: 'Chlorine dispensers at communal water points, paired with door-to-door household education on safe water treatment, one of the highest-evidence, lowest-cost ways to prevent diarrhea.',
  },
  '/portfolio/mental-health': {
    title: 'Group Therapy for Depression | Connect by Dimagi',
    desc: 'Connect trains local facilitators to run structured weekly group therapy for depression, with every session app-guided, verified, and paid only when confirmed.',
  },
  '/portfolio/survey-data-collection': {
    title: 'Connect Interview | Connect by Dimagi',
    desc: 'Connect Interview turns Frontline Workers into a rapid research network. Stakeholders submit questions, an AI chatbot interviews workers in-app, and Dimagi delivers transcripts within two weeks.',
  },
  '/portfolio/therapeutic-food': {
    title: 'Therapeutic Food | Connect by Dimagi',
    desc: 'Frontline Workers deliver home-based malnutrition treatment with Ready-to-Use Therapeutic Food (RUTF). Every visit is verified with GPS, timestamps, and photos.',
  },
  '/portfolio/rooftop-sampling': {
    title: 'Rooftop Sampling | Connect by Dimagi',
    desc: 'A GPS-navigated household survey method that uses satellite building footprints as the sampling frame, no household list required. Developed by IDinsight.',
  },
};

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
  document.title = m.title;
  setMetaAttr('meta[name="description"]', 'content', m.desc);
  setMetaAttr('meta[property="og:title"]', 'content', m.title);
  setMetaAttr('meta[property="og:description"]', 'content', m.desc);
  setMetaAttr('meta[name="twitter:title"]', 'content', m.title);
  setMetaAttr('meta[name="twitter:description"]', 'content', m.desc);
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
  // Insights deep-linking: ?program=… &activity=… preselect the filters. The
  // search is threaded in (not read off location) so it works on file:// too,
  // where the URL can't carry a query. Falls back to the live URL on direct hits.
  if (currentRoute === '/insights') {
    applyInsightFiltersFromQuery(
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

// Insights, two-axis filter (Program Type × Frontline Activity)
let activeProgram = 'all';
let activeLDVP = 'all';

// The URL is the FULL filter spec for the insights page: ?program=<chc|kmc|ecd>
// and ?activity=<learn|deliver|verify|pay> (the LDVP axis). An axis with no (or
// an unknown) param resets to "all", so a deep-link like ?program=chc never
// inherits a stale filter left over from an earlier pill click. Called by
// render() whenever /insights is shown.
function applyInsightFiltersFromQuery(search) {
  const params = new URLSearchParams(search || '');
  [
    { axis: 'program', value: params.get('program') || 'all' },
    { axis: 'ldvp', value: params.get('activity') || 'all' },
  ].forEach(({ axis, value }) => {
    const group = document.querySelector(
      '.filter-pills[data-filter-group="' + axis + '"]',
    );
    if (!group) return;
    // Fall back to "all" if the requested value isn't a real pill.
    const pill =
      group.querySelector('.pill[data-filter-value="' + value + '"]') ||
      group.querySelector('.pill[data-filter-value="all"]');
    if (!pill) return;
    const resolved = pill.getAttribute('data-filter-value');
    group
      .querySelectorAll('.pill')
      .forEach((p) => p.classList.remove('active'));
    pill.classList.add('active');
    if (axis === 'program') activeProgram = resolved;
    else activeLDVP = resolved;
  });
  applyInsightFilters();
}

function applyInsightFilters() {
  document
    .querySelectorAll('[data-page="/insights"] .insight-row')
    .forEach((row) => {
      const programs = (row.dataset.programs || '')
        .split(/\s+/)
        .filter(Boolean);
      const ldvps = (row.dataset.ldvp || '').split(/\s+/).filter(Boolean);
      const programMatch =
        activeProgram === 'all' || programs.includes(activeProgram);
      const ldvpMatch = activeLDVP === 'all' || ldvps.includes(activeLDVP);
      row.classList.toggle('is-hidden', !(programMatch && ldvpMatch));
    });
}

document.addEventListener('click', (e) => {
  const pill = e.target.closest('.filter-pills .pill');
  if (!pill) return;
  const type = pill.dataset.filterType;
  const value = pill.dataset.filterValue;
  if (!type || !value) return;
  // Insights page filters only
  if (type !== 'program' && type !== 'ldvp') return;
  if (type === 'program') activeProgram = value;
  if (type === 'ldvp') activeLDVP = value;
  pill.parentElement
    .querySelectorAll('.pill')
    .forEach((p) => p.classList.remove('active'));
  pill.classList.add('active');
  applyInsightFilters();
});

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
