/* Draws the story thread, places the party badges, and runs the counters.
   Loaded only by templates/prelogin/in-action.html. */
(function () {
  'use strict';
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.documentElement.classList.add('js');

  /* The sticky date rail sits below the site header, whose height is set by
     styles.css rather than by this page. Measure it instead of hard-coding, so
     a change there cannot leave a gap or an overlap. */
  var page = document.querySelector('.cia');
  function syncHeaderOffset() {
    var hdr = document.querySelector('.site-header');
    if (hdr && page)
      page.style.setProperty(
        '--hdr-h',
        Math.round(hdr.getBoundingClientRect().height) + 'px',
      );
  }
  syncHeaderOffset();
  window.addEventListener('resize', syncHeaderOffset);

  var ROLE_COLOR = {
    fund: '#0b8a82',
    conn: '#3843d0',
    front: '#c0381a',
    note: '#5a6172',
  };
  var ROLE_NAME = {
    fund: 'Funder',
    conn: 'Connect Platform',
    front: 'Frontline Organization',
  };

  var body = document.querySelector('.cia-tl-body');
  var svg = document.getElementById('cia-ribbon');
  var halo = document.getElementById('cia-ribbon-halo');
  var track = document.getElementById('cia-ribbon-track');
  var path = document.getElementById('cia-ribbon-path');
  var head = document.getElementById('cia-ribbon-head');
  var endEl = document.getElementById('cia-ribbon-end');
  var grad = document.getElementById('cia-ribbon-grad');
  var defs = grad.parentNode;
  var railEl = document.getElementById('cia-date-rail');

  /* Waypoints are the row-level blocks: side cards, centred Connect cards,
     whole-width photos and screens, and paired rows (where the thread runs
     down the gutter between the two things). */
  var stops = Array.prototype.slice.call(
    document.querySelectorAll(
      '.cia-rows > .cia-beat, .cia-rows > .cia-shot, .cia-rows > .cia-pair',
    ),
  );

  /* One node of art per party, cloned into each badge. */
  var ART = {};
  Array.prototype.forEach.call(
    document.querySelectorAll('#cia-role-art img'),
    function (img) {
      ART[img.dataset.art] = img;
    },
  );
  var connMark = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  connMark.setAttribute('viewBox', '0 0 1526 431');
  connMark.setAttribute('aria-hidden', 'true');
  connMark.innerHTML = '<use href="#connect-logo"/>';
  ART.conn = connMark;

  var samples = [],
    nodes = [],
    railItems = [],
    pathLen = 0,
    bodyH = 0;
  /* Rows where Connect stands between the two sides: the arcs reaching out from
     it, the badges they land on, and which waypoints are those hubs. */
  var bridges = [],
    hubs = {};
  /* Half a badge, so an arc can be drawn along the badges' centre line. */
  var NODE_R = 31;

  var SVGNS = 'http://www.w3.org/2000/svg';
  function tiePath(cls, d) {
    var el = document.createElementNS(SVGNS, 'path');
    el.setAttribute('class', cls);
    el.setAttribute('d', d);
    svg.insertBefore(
      el,
      head,
    ); /* over the main thread, under the drawing head */
    return el;
  }

  railEl.innerHTML =
    '<b>Timeline</b>' +
    stops
      .map(function (el, i) {
        if (!el.dataset.rail) return '';
        return (
          '<span data-i="' +
          i +
          '" data-role="' +
          el.dataset.role +
          '">' +
          el.dataset.rail +
          '</span>'
        );
      })
      .join('');
  railItems = Array.prototype.slice
    .call(railEl.querySelectorAll('span'))
    .map(function (s) {
      return { el: s, i: +s.dataset.i };
    });

  /* &#9472;&#9472; Build the thread from where the blocks actually landed &#9472;&#9472;
     Two waypoints per block, at its top and bottom edge on the block's centre
     line, so the thread runs dead straight through it and does all its swinging
     in the gap between blocks. A single centre waypoint would leave the line
     still crossing sideways at the block's top edge. */
  function buildRibbon() {
    var stacked = window.innerWidth <= 860;
    var br = body.getBoundingClientRect();
    var W = body.clientWidth,
      H = body.clientHeight;
    bodyH = H;
    if (!W || !H) return;

    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);

    /* Green keeps to the left lane, red to the right, Connect down the middle,
       so a glance at the line already says whose move this is. */
    var cs = getComputedStyle(document.querySelector('.cia-rows'));
    var colW = (W - (parseFloat(cs.columnGap) || 0)) / 2;
    function laneX(role, fallback) {
      if (role === 'fund') return colW / 2;
      if (role === 'front') return W - colW / 2;
      if (role === 'conn' || role === 'note') return W / 2;
      return fallback;
    }

    var cards = stops.map(function (el) {
      var r = el.getBoundingClientRect();
      return {
        /* Stacked: one rail down the left gutter. Wide: the block's own centre. */
        x: stacked
          ? 27
          : laneX(el.dataset.role, r.left - br.left + r.width / 2),
        top: r.top - br.top,
        bottom: r.bottom - br.top,
        role: el.dataset.role,
      };
    });

    /* Open from the right of the frame and sweep left into the first block,
       so the thread arrives rather than simply dropping in. */
    var sx = stacked ? cards[0].x : W * 0.82,
      sy = Math.max(0, cards[0].top - 130);
    var d =
      'M ' +
      sx +
      ' 0 C ' +
      sx +
      ' ' +
      sy * 0.55 +
      ' ' +
      cards[0].x +
      ' ' +
      (cards[0].top - sy * 0.45) +
      ' ' +
      cards[0].x +
      ' ' +
      cards[0].top +
      ' L ' +
      cards[0].x +
      ' ' +
      cards[0].bottom;
    for (var i = 1; i < cards.length; i++) {
      var a = cards[i - 1],
        c = cards[i],
        dy = (c.top - a.bottom) * 0.5;
      d +=
        ' C ' +
        a.x +
        ' ' +
        (a.bottom + dy) +
        ' ' +
        c.x +
        ' ' +
        (c.top - dy) +
        ' ' +
        c.x +
        ' ' +
        c.top +
        ' L ' +
        c.x +
        ' ' +
        c.bottom;
    }

    halo.setAttribute('d', d);
    track.setAttribute('d', d);
    path.setAttribute('d', d);

    /* Colour the thread by whichever side it is crossing. The path only ever
       descends, so a plain vertical gradient tracks it exactly. */
    grad.setAttribute('y2', H);
    var g =
      '<stop offset="0%" stop-color="' + ROLE_COLOR[cards[0].role] + '"/>';
    cards.forEach(function (c, k) {
      var col = ROLE_COLOR[c.role];
      g +=
        '<stop offset="' +
        ((c.top / H) * 100).toFixed(3) +
        '%" stop-color="' +
        col +
        '"/>' +
        '<stop offset="' +
        ((c.bottom / H) * 100).toFixed(3) +
        '%" stop-color="' +
        col +
        '"/>';
      if (k === cards.length - 1)
        g += '<stop offset="100%" stop-color="' + col + '"/>';
    });
    grad.innerHTML = g;

    pathLen = path.getTotalLength();
    path.style.strokeDasharray = pathLen;
    halo.style.strokeDasharray = pathLen;

    samples = [];
    for (var s = 0; s <= 700; s++) {
      var l = (pathLen * s) / 700,
        pt = path.getPointAtLength(l);
      samples.push({ x: pt.x, y: pt.y, len: l });
    }

    /* The thread finishes on a filled mark in the last party's colour, so it
       closes rather than simply stopping. */
    var last = samples[samples.length - 1],
      lastRole = cards[cards.length - 1].role;
    endEl.setAttribute('cx', last.x);
    endEl.setAttribute('cy', last.y);
    endEl.setAttribute('stroke', ROLE_COLOR[lastRole]);

    /* Where Connect stands between the two sides, all three parties stand on
       the block: the thread comes down the middle to Connect, and an arc
       reaches out from there to the funder on one side and the frontline
       organization on the other. Stacked, there is one lane and no room to
       reach across, so the arcs are left out. */
    bridges.forEach(function (b) {
      b.halo.remove();
      b.path.remove();
      b.grad.remove();
      b.nodes.forEach(function (n) {
        n.el.remove();
        n.cap.remove();
      });
    });
    bridges = [];
    hubs = {};
    if (!stacked) {
      Array.prototype.forEach.call(
        document.querySelectorAll('[data-bridge]'),
        function (el, k) {
          var i = stops.indexOf(el.closest('[data-row]'));
          if (i < 0) return;
          hubs[i] = true;
          var hub = cards[i];
          var cy = hub.top - NODE_R; /* the badges' centre line */

          /* Every party on this block, in the order they stand across it. */
          var pts = el.dataset.bridge.split(',').map(function (role) {
            return { role: role, x: laneX(role, hub.x) };
          });
          pts.push({ role: hub.role, x: hub.x });
          pts.sort(function (a, b) {
            return a.x - b.x;
          });
          var x1 = pts[0].x,
            x2 = pts[pts.length - 1].x;

          var gid = 'in-action-tie-' + k;
          var g2 = document.createElementNS(SVGNS, 'linearGradient');
          g2.setAttribute('id', gid);
          g2.setAttribute('gradientUnits', 'userSpaceOnUse');
          g2.setAttribute('x1', x1);
          g2.setAttribute('y1', cy);
          g2.setAttribute('x2', x2);
          g2.setAttribute('y2', cy);
          g2.innerHTML = pts
            .map(function (pt) {
              return (
                '<stop offset="' +
                (((pt.x - x1) / (x2 - x1)) * 100).toFixed(2) +
                '%" stop-color="' +
                ROLE_COLOR[pt.role] +
                '"/>'
              );
            })
            .join('');
          defs.appendChild(g2);

          /* A row of dots between the badges, on a softly bent path: a route
           rather than a pipe. Only the gaps bend, so the badges stay level. */
          var d2 = '';
          for (var q = 1; q < pts.length; q++) {
            var xa = pts[q - 1].x,
              xb = pts[q].x,
              m = (xb - xa) * 0.4;
            d2 +=
              ' M ' +
              xa +
              ' ' +
              cy +
              ' C ' +
              (xa + m) +
              ' ' +
              (cy - 13) +
              ' ' +
              (xb - m) +
              ' ' +
              (cy - 13) +
              ' ' +
              xb +
              ' ' +
              cy;
          }
          d2 = d2.trim();
          var halo = tiePath('cia-tie-halo', d2),
            tie = tiePath('cia-tie', d2);
          tie.setAttribute('stroke', 'url(#' + gid + ')');
          /* Revealed by fading in, not by drawing: a stroke-dasharray reveal would
           have to overwrite the dash pattern that makes the dots. */
          [halo, tie].forEach(function (pp) {
            pp.style.opacity = '0';
          });

          var ns = [];
          pts.forEach(function (pt) {
            if (pt.role === hub.role)
              return; /* the hub's own badge comes from the main thread */
            var node = document.createElement('span');
            node.className = 'cia-node';
            node.dataset.role = pt.role;
            if (ART[pt.role]) node.appendChild(ART[pt.role].cloneNode(true));
            node.style.left = (pt.x / W) * 100 + '%';
            node.style.top = hub.top + 'px';
            body.appendChild(node);

            var cap = document.createElement('span');
            cap.className = 'cia-node-cap';
            cap.setAttribute('aria-hidden', 'true');
            cap.dataset.role = pt.role;
            cap.textContent = ROLE_NAME[pt.role];
            cap.style.left = (pt.x / W) * 100 + '%';
            cap.style.top = hub.top - 92 + 'px';
            body.appendChild(cap);

            ns.push({ el: node, cap: cap });
          });

          bridges.push({
            halo: halo,
            path: tie,
            grad: g2,
            at: sampleAtY(hub.top).len,
            nodes: ns,
          });
        },
      );
    }

    nodes.forEach(function (n) {
      n.el.remove();
      if (n.cap) n.cap.remove();
    });
    nodes = cards.map(function (c, i) {
      var el = document.createElement('span');
      el.className = 'cia-node' + (c.role === 'note' ? ' cia-plain' : '');
      el.dataset.role = c.role;
      var art = ART[c.role];
      if (art) el.appendChild(art.cloneNode(true));
      el.style.left = (c.x / W) * 100 + '%';
      el.style.top = c.top + 'px';
      body.appendChild(el);

      var cap = null;
      if (ROLE_NAME[c.role] && !hubs[i]) {
        /* If the previous block sat in the same lane, the thread drops straight
           down through the space above this badge, so the label moves aside. */
        var prevX = i > 0 ? cards[i - 1].x : sx;
        /* Except at the hub, where two more badges stand either side of this
           one: all three are labelled the same way, and the chip's own ground
           colour masks the thread coming down behind it. */
        var aside = Math.abs(prevX - c.x) < 70 && !hubs[i];
        cap = document.createElement('span');
        cap.className = 'cia-node-cap' + (aside ? ' cia-aside' : '');
        cap.setAttribute('aria-hidden', 'true');
        cap.dataset.role = c.role;
        cap.textContent = ROLE_NAME[c.role];
        cap.style.left = (c.x / W) * 100 + '%';
        cap.style.top =
          c.top -
          (aside ? (stacked ? 20 : 31) : stacked ? 48 : hubs[i] ? 92 : 72) +
          'px';
        body.appendChild(cap);
      }
      return { el: el, cap: cap, len: sampleAtY(c.top).len, role: c.role };
    });

    draw();
  }

  function sampleAtY(y) {
    if (!samples.length) return { x: 0, y: 0, len: 0 };
    var lo = 0,
      hi = samples.length - 1;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (samples[mid].y < y) lo = mid + 1;
      else hi = mid;
    }
    return samples[lo];
  }

  function draw() {
    if (!pathLen) return;
    var br = body.getBoundingClientRect();
    /* The drawing head tracks a line ~62% down the viewport, so the thread
       reaches each block just as that block comes into reading position. */
    var headY = window.innerHeight * 0.62 - br.top;
    var drawn =
      reduce || headY >= bodyH
        ? pathLen
        : headY <= 0
        ? 0
        : sampleAtY(headY).len;

    path.style.strokeDashoffset = pathLen - drawn;
    halo.style.strokeDashoffset = pathLen - drawn;

    var showHead = drawn > 0 && drawn < pathLen && !reduce;
    head.style.opacity = showHead ? '1' : '0';
    if (showHead) {
      var p = path.getPointAtLength(drawn);
      head.setAttribute('cx', p.x);
      head.setAttribute('cy', p.y);
    }

    var reached = -1,
      role = nodes.length ? nodes[0].role : 'conn';
    nodes.forEach(function (n, i) {
      var passed = drawn >= n.len;
      n.el.classList.toggle('cia-on', passed);
      if (n.cap) n.cap.classList.toggle('cia-on', passed);
      if (passed) {
        reached = i;
        role = n.role;
      }
    });
    head.setAttribute('stroke', ROLE_COLOR[role]);

    /* Each row's rail appears once the main thread has arrived at its hub. */
    bridges.forEach(function (b) {
      var out = drawn >= b.at;
      b.path.style.opacity = out ? '1' : '0';
      b.halo.style.opacity = out ? '1' : '0';
      b.nodes.forEach(function (n) {
        n.el.classList.toggle('cia-on', out);
        n.cap.classList.toggle('cia-on', out);
      });
    });

    endEl.style.opacity = drawn >= pathLen - 1 ? '1' : '0';

    var lastLit = -1;
    railItems.forEach(function (r, k) {
      if (r.i <= reached) lastLit = k;
    });
    railItems.forEach(function (r, k) {
      r.el.classList.toggle('cia-done', k < lastLit);
      r.el.classList.toggle('cia-on', k === lastLit);
    });
  }

  /* &#9472;&#9472; Parallax, on the two full-bleed photographs only &#9472;&#9472; */
  var parallax = Array.prototype.slice.call(
    document.querySelectorAll('[data-par]'),
  );
  parallax.forEach(function (el) {
    el.style.transform = 'scale(1.14)';
  });
  function moveParallax() {
    if (reduce) return;
    var vh = window.innerHeight;
    parallax.forEach(function (el) {
      var r = el.getBoundingClientRect();
      if (r.bottom < -200 || r.top > vh + 200) return;
      var offset = (r.top + r.height / 2 - vh / 2) * parseFloat(el.dataset.par);
      el.style.transform =
        'translate3d(0,' + offset.toFixed(1) + 'px,0) scale(1.14)';
    });
  }

  /* The screen settles into place as it comes up the page. The growth tops out
     at the element's own laid-out size, so it stays inside its box and can
     never reach over the block below it. */
  var grows = Array.prototype.slice.call(
    document.querySelectorAll('[data-grow]'),
  );
  var GROW_FROM = 0.88;
  function moveGrow() {
    var vh = window.innerHeight;
    grows.forEach(function (el) {
      if (reduce) {
        el.style.setProperty('--g', '1');
        return;
      }
      var r = el.getBoundingClientRect();
      /* 0 as its top crosses into view, 1 once it has risen to two-thirds up. */
      var t = (vh - r.top) / (vh * 0.66);
      t = t < 0 ? 0 : t > 1 ? 1 : t;
      el.style.setProperty('--g', (GROW_FROM + (1 - GROW_FROM) * t).toFixed(4));
    });
  }

  var progressBar = document.getElementById('cia-progress-bar');
  function moveProgress() {
    var h = document.documentElement.scrollHeight - window.innerHeight;
    progressBar.style.width =
      (h > 0 ? Math.min(100, (window.scrollY / h) * 100) : 0) + '%';
  }

  var ticking = false;
  window.addEventListener(
    'scroll',
    function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        ticking = false;
        draw();
        moveParallax();
        moveGrow();
        moveProgress();
      });
    },
    { passive: true },
  );

  var resizeTimer = null;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      buildRibbon();
      moveParallax();
      moveGrow();
    }, 160);
  });

  /* &#9472;&#9472; Reveal, the locator animation, and count-up on the figures &#9472;&#9472; */
  var fmt = function (n) {
    return n.toLocaleString('en-US');
  };
  if ('IntersectionObserver' in window) {
    var ro = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          e.target.classList.add('cia-in');
          ro.unobserve(e.target);
        });
      },
      { rootMargin: '0px 0px -8% 0px', threshold: 0.08 },
    );
    document.querySelectorAll('.cia-rv').forEach(function (n) {
      ro.observe(n);
    });

    var lo = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          e.target.classList.add('cia-in');
          lo.unobserve(e.target);
        });
      },
      { threshold: 0.15, rootMargin: '0px 0px -5% 0px' },
    );
    document.querySelectorAll('.cia-locator').forEach(function (n) {
      lo.observe(n);
    });

    if (!reduce) {
      var co = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (e) {
            if (!e.isIntersecting) return;
            co.unobserve(e.target);
            var el = e.target,
              to = +el.dataset.to,
              t0 = performance.now();
            (function tick(now) {
              var p = Math.min(1, (now - t0) / 1400);
              el.textContent = fmt(Math.round(to * (1 - Math.pow(1 - p, 3))));
              if (p < 1) requestAnimationFrame(tick);
            })(t0);
          });
        },
        { threshold: 0.5 },
      );
      document.querySelectorAll('[data-to]').forEach(function (n) {
        co.observe(n);
      });
    }
  } else {
    document.querySelectorAll('.cia-rv, .cia-locator').forEach(function (n) {
      n.classList.add('cia-in');
    });
  }

  /* The thread is measured from laid-out blocks, so wait for webfonts and
     images to settle before building it, and rebuild once everything loaded. */
  function boot() {
    buildRibbon();
    moveParallax();
    moveGrow();
    moveProgress();
  }
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(boot);
  else boot();
  window.addEventListener('load', boot);
  setTimeout(boot, 400);
})();
