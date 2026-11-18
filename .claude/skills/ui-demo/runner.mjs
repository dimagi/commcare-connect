// runner.mjs — ui-demo step runner (Verify Run + Record Pass). Copy into the task folder next to flow.mjs.
//
//   import { run, expect } from './runner.mjs';
//   await run({ BASE, INTRO, OUTRO, STEPS, preAuth: (page) => c.quickLogin(page, c.USERS.admin), firstUrl: c.urls.orgHome }, import.meta.url);
//
// Start where the feature starts: `preAuth` signs in OFF camera (plain Playwright, before the screencast starts) and
// `firstUrl` is the feature's page. Put a login step in STEPS only when signing in IS part of the feature
// (auth, invites, permissions). A demo that opens on the login screen for an unrelated feature wastes its first 10 s.
//
// `node flow.mjs`           → Verify Run: headless, plain actions, assertions, checkpoints → output/report.json
// `node flow.mjs --record`  → Record Pass: headed, 2x capture, spring cursor, ring/ripple, human typing, captions,
//                             intro/outro cards → output/raw.mp4 + output/steps.json
//
// Step schema and rationale: reference/verify-runner.md. Rules learned the hard way: reference/learnings.md.
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { chromium } from 'playwright';
import { expect } from '@playwright/test';

export { expect };

export const RECORD = process.argv.includes('--record');
const FROM = process.argv.find((a) => a.startsWith('--from='))?.split('=')[1];

const DEFAULTS = {
  VIEW: { width: 1280, height: 800 }, // UI text stays readable when Slack/GitHub play the video at ~600-800 px wide
  DPR: 2, // record-only: headed Chromium captures true 2x frames; headless would ignore it
  FPS: 30,
  useState: true, // reuse auth between Verify Runs; set false for flows that switch identity mid-run (clearCookies)
  preAuth: null, // async (page) => {...}: sign in before anything is captured (and before a Verify Run without saved state)
  failRegion: 'main, form, [role=main]',
  maxSteps: 12,
  testDataRe: /\b(test\d*|demo|dummy|uitest|example\.com|lorem|foo|bar)\b/i,
  accent: '#ed6a14',
  introMs: 3000,
  outroMs: 3000,
};

export async function run(config, flowUrl) {
  const cfg = { ...DEFAULTS, ...config };
  const { BASE, VIEW, DPR, FPS, INTRO, OUTRO, STEPS } = cfg;
  const OUTPUT_DIR =
    cfg.outputDir ?? path.join(path.dirname(fileURLToPath(flowUrl)), 'output');
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  const STATE = path.join(OUTPUT_DIR, 'state.json');

  let t0 = Date.now();
  const now = () => (Date.now() - t0) / 1000; // recording seconds; t0 is reset when the screencast starts

  // ---------- overlays: only page.screencast.showOverlay/showChapter are captured reliably (never page.evaluate DOM)
  async function ring(page, b, duration = 700) {
    await page.screencast.showOverlay(
      `<div style="position:absolute;left:${b.x - 6}px;top:${b.y - 6}px;width:${
        b.width + 12
      }px;height:${b.height + 12}px;
        border:3px solid ${
          cfg.accent
        };border-radius:10px;box-shadow:0 0 0 2px #fff,0 0 14px rgba(237,106,20,.7)"></div>`,
      { duration },
    );
  }
  function ripple(page, p, duration = 450) {
    return page.screencast.showOverlay(
      `<style>@keyframes pwr{from{transform:scale(.3);opacity:1}to{transform:scale(2.2);opacity:0}}</style>
       <div style="position:absolute;left:${p.x - 20}px;top:${
         p.y - 20
       }px;width:40px;height:40px;border-radius:50%;
        border:3px solid ${
          cfg.accent
        };box-shadow:0 0 0 1px #fff;animation:pwr ${
          duration - 30
        }ms ease-out forwards"></div>`,
      { duration },
    );
  }

  // ---------- cursor: the macOS arrow as a sticky overlay re-added at each spring tick.
  // Playwright's overlay stylesheet has `svg { position:absolute; height:0 }` → the <svg> MUST carry inline size.
  // Never use page.screencast.showActions(): every action then blocks for the annotation duration.
  const CURSOR_PX = 40; // box → arrow ≈ 30 px tall; tip at (8.2, 4.9) of the 28×28 grid is anchored on the pointer
  const CURSOR_TIP = { x: 8.2 * (CURSOR_PX / 28), y: 4.9 * (CURSOR_PX / 28) };
  const CURSOR_SVG = `<svg viewBox="0 0 28 28" style="position:static;width:${CURSOR_PX}px;height:${CURSOR_PX}px;filter:drop-shadow(0 2px 3px rgba(0,0,0,.45))">
    <polygon fill="#FFFFFF" points="8.2,20.9 8.2,4.9 19.8,16.5 13,16.5 12.6,16.6"/>
    <polygon fill="#FFFFFF" points="17.3,21.6 13.7,23.1 9,12 12.7,10.5"/>
    <rect x="12.5" y="13.6" transform="matrix(0.9221 -0.3871 0.3871 0.9221 -5.7605 6.5909)" width="2" height="8"/>
    <polygon points="9.2,7.3 9.2,18.5 12.2,15.6 12.6,15.5 17.4,15.5"/>
  </svg>`;
  let cursor = { x: VIEW.width * 0.62, y: VIEW.height * 0.7 }; // off-centre: the centre is where chapter cards sit
  let cursorOverlay = null;
  async function drawCursor(page, p) {
    const next = await page.screencast.showOverlay(
      `<div style="position:absolute;left:${p.x - CURSOR_TIP.x}px;top:${
        p.y - CURSOR_TIP.y
      }px;z-index:2">${CURSOR_SVG}</div>`,
    );
    if (cursorOverlay) await cursorOverlay.dispose();
    cursorOverlay = next;
  }
  // Spring glide (Cap "Mellow": tension 470, mass 3, friction 70 @60 Hz). Target is the click point from tick one
  // (click look-ahead); hard snap + 120 ms landing beat so the tip is exactly on the element when the ripple fires.
  async function glide(
    page,
    to,
    { tension = 470, mass = 3, friction = 70, maxMs = 1200 } = {},
  ) {
    const dt = 1 / 60;
    const pos = { ...cursor },
      vel = { x: 0, y: 0 };
    const start = Date.now();
    while (Date.now() - start < maxMs) {
      const ax = (-tension * (pos.x - to.x) - friction * vel.x) / mass;
      const ay = (-tension * (pos.y - to.y) - friction * vel.y) / mass;
      vel.x += ax * dt;
      vel.y += ay * dt;
      pos.x += vel.x * dt;
      pos.y += vel.y * dt;
      await page.mouse.move(pos.x, pos.y);
      await drawCursor(page, pos);
      if (
        Math.hypot(pos.x - to.x, pos.y - to.y) < 0.75 &&
        Math.hypot(vel.x, vel.y) < 15
      )
        break;
      await page.waitForTimeout(16);
    }
    await page.mouse.move(to.x, to.y);
    await drawCursor(page, to);
    cursor = { ...to };
    await page.waitForTimeout(120);
  }

  // ---------- log for render.py (recording seconds, CSS px): spots drive the spotlight dim
  const spots = [];
  const navigations = [];
  let lastTarget = null;

  // ---------- demo helpers: plain Playwright in verify mode, polished under --record
  const d = {
    async click(page, selector, { highlight = true, spot = true } = {}) {
      const el =
        typeof selector === 'string'
          ? page.locator(selector).first()
          : selector;
      if (!RECORD) return el.click();
      await el.scrollIntoViewIfNeeded();
      const box = await el.boundingBox();
      if (!box) return el.click();
      lastTarget = { x: box.x, y: box.y, width: box.width, height: box.height };
      const spotStart = now();
      if (highlight) await ring(page, box);
      await glide(page, {
        x: box.x + box.width / 2,
        y: box.y + box.height / 2,
      });
      const r = ripple(page, cursor); // not awaited: ripple and click coincide
      await el.click();
      await r; // ~450 ms post-click hold
      if (spot) spots.push({ start: spotStart, end: now(), box: lastTarget });
    },
    async type(page, selector, text, { delay = 60 } = {}) {
      const el =
        typeof selector === 'string'
          ? page.locator(selector).first()
          : selector;
      if (!RECORD) return el.fill(text);
      const spotStart = now();
      await d.click(page, el, { highlight: false, spot: false });
      await el.fill('');
      await el.pressSequentially(text, { delay });
      spots.push({ start: spotStart, end: now() + 0.3, box: lastTarget });
    },
    /** Glide onto an element without clicking, for hover-only UI (tooltips). A click would close tippy tooltips. */
    async hover(page, selector) {
      const el =
        typeof selector === 'string'
          ? page.locator(selector).first()
          : selector;
      if (!RECORD) return el.hover();
      await el.scrollIntoViewIfNeeded();
      const box = await el.boundingBox();
      if (!box) return el.hover();
      lastTarget = { x: box.x, y: box.y, width: box.width, height: box.height };
      await glide(page, {
        x: box.x + box.width / 2,
        y: box.y + box.height / 2,
      }); // the real mouse follows the glide
    },
    async select(page, selector, option) {
      const el =
        typeof selector === 'string'
          ? page.locator(selector).first()
          : selector;
      if (RECORD) await d.click(page, el, { highlight: false });
      await el.selectOption(option);
    },
    async check(page, selector) {
      const el =
        typeof selector === 'string'
          ? page.locator(selector).first()
          : selector;
      if (RECORD) await d.click(page, el, { highlight: false });
      else await el.check();
      await el.check().catch(() => {});
    },
    async goto(page, url) {
      await page.goto(url);
      await page.waitForLoadState('networkidle').catch(() => {});
    },
    async pause(page, ms) {
      if (RECORD) await page.waitForTimeout(ms); // a deliberate beat the viewer needs (e.g. after a toast appears)
    },
    /** Sticky "NEW" style tag next to an element while a step runs; returns a disposable (record-only). */
    async badge(page, selector, text = 'NEW') {
      if (!RECORD) return { dispose: async () => {} };
      const b = await page.locator(selector).first().boundingBox();
      if (!b) return { dispose: async () => {} };
      return page.screencast.showOverlay(
        `<div style="position:absolute;left:${b.x + b.width + 10}px;top:${
          b.y - 4
        }px;padding:3px 9px;background:${cfg.accent};color:#fff;
          border-radius:999px;font:700 13px/1.4 system-ui;letter-spacing:.04em;box-shadow:0 1px 4px rgba(0,0,0,.3)">${text}</div>`,
      );
    },
    now,
  };

  function holdMs(title, subtitle) {
    const chars = `${title} ${subtitle || ''}`.trim().length;
    return Math.min(
      7000,
      Math.max(1500, Math.round((chars / 20 + 0.6) * 1000)),
    ); // 20 chars/s + settle
  }

  // ---------- browser
  const browser = await chromium.launch({ headless: !RECORD }); // --record is headed: required for 2x capture
  const hasSavedState = cfg.useState && fs.existsSync(STATE) && !RECORD;
  const context = await browser.newContext({
    viewport: VIEW,
    deviceScaleFactor: RECORD ? DPR : 1,
    ...(hasSavedState ? { storageState: STATE } : {}),
  });
  const page = await context.newPage();
  page.on('framenavigated', (f) => {
    if (f === page.mainFrame() && RECORD) navigations.push(now());
  });
  if (cfg.preAuth && !hasSavedState) await cfg.preAuth(page); // off camera: nothing is recording yet

  const report = {
    ok: true,
    mode: RECORD ? 'record' : 'verify',
    steps: [],
    warnings: [],
    consoleErrors: [],
    failedRequests: [],
  };
  page.on(
    'console',
    (m) =>
      m.type() === 'error' && report.consoleErrors.push(m.text().slice(0, 300)),
  );
  page.on(
    'response',
    (r) =>
      r.status() >= 400 &&
      report.failedRequests.push(
        `${r.status()} ${r.request().method()} ${r.url()}`,
      ),
  );

  if (STEPS.length > cfg.maxSteps)
    report.warnings.push(
      `${STEPS.length} steps — demos past ${cfg.maxSteps} lose viewers; split the flow`,
    );
  for (const s of STEPS) {
    const [t, sub] = s.caption ?? [];
    if (t && t.length > 42)
      report.warnings.push(`step ${s.name}: title ${t.length} chars (>42)`);
    if (sub && sub.length > 60)
      report.warnings.push(
        `step ${s.name}: subtitle ${sub.length} chars (>60)`,
      );
    if (t && !s.intent)
      report.warnings.push(
        `step ${s.name}: no intent — the viewer won't know what to watch during the action`,
      );
    if (s.intent && s.intent.length > 42)
      report.warnings.push(
        `step ${s.name}: intent ${s.intent.length} chars (>42)`,
      );
  }

  // ---------- record setup: first frame is a real page, then the intro card
  const stepsLog = [];
  let intro = null;
  let capture = null;
  if (RECORD) {
    await page.goto(cfg.firstUrl ?? BASE); // avoids a blank first frame / thumbnail
    if (cfg.preAuth && /\/accounts\/login/.test(page.url())) {
      // seen once right after a server start: the session from preAuth was not honoured — sign in again, still off camera
      await cfg.preAuth(page);
      await page.goto(cfg.firstUrl ?? BASE);
    }
    capture = frameCapture(path.join(OUTPUT_DIR, 'raw.mp4'), FPS);
    await page.screencast.start({
      size: { width: VIEW.width * DPR, height: VIEW.height * DPR },
      quality: 100,
      onFrame: capture.onFrame,
    });
    const introCard = page.screencast.showChapter(INTRO.title, {
      description: INTRO.description,
      duration: cfg.introMs,
    });
    await page.waitForTimeout(CHAPTER_FADE_MS); // frame 0 (the Slack/GitHub thumbnail) is the fully faded-in intro card
    t0 = Date.now();
    capture.begin(t0);
    await introCard;
    await drawCursor(page, cursor); // after the card, so the pointer never sits on its text
    intro = { start: 0, end: now() };
  }

  // ---------- step loop
  let started = !FROM;
  for (const step of STEPS) {
    if (!started) {
      started = step.name === FROM;
      if (!started) continue;
    }
    const entry = { name: step.name, ok: false, ms: 0 };
    const wall = Date.now();
    const actionStart = now();
    const urlBefore = page.url();
    lastTarget = null;
    try {
      await step.run(page, d);
      if (step.assert) await step.assert(page);
      const actionEnd = now();
      if (RECORD && step.caption)
        await page.waitForTimeout(holdMs(...step.caption));
      if (RECORD) {
        const [title, subtitle] = step.caption ?? [];
        stepsLog.push({
          name: step.name,
          intent: step.intent ?? null,
          title,
          subtitle,
          actionStart,
          actionEnd,
          holdEnd: now(),
          target: lastTarget,
          navigated: page.url() !== urlBefore,
          captionPos: step.captionPos ?? null,
          // where the outcome is on screen, so render.py keeps the caption off it
          result: step.checkpoint
            ? await page
                .locator(step.checkpoint)
                .first()
                .boundingBox()
                .catch(() => null)
            : null,
        });
      }
      if (!RECORD && step.checkpoint) {
        entry.checkpoint = await page
          .locator(step.checkpoint)
          .first()
          .ariaSnapshot();
        const m = entry.checkpoint.match(cfg.testDataRe);
        if (m)
          report.warnings.push(
            `step ${step.name}: test-looking data on screen ("${m[0]}") — reseed with realistic values`,
          );
      }
      entry.ok = true;
    } catch (err) {
      report.ok = false;
      entry.error = String(err).split('\n').slice(0, 8).join('\n');
      entry.url = page.url();
      entry.title = await page.title().catch(() => '');
      entry.failRegion = await page
        .locator(step.failRegion ?? cfg.failRegion)
        .first()
        .ariaSnapshot()
        .catch(() => null);
      await page
        .screenshot({ path: path.join(OUTPUT_DIR, `fail-${step.name}.png`) })
        .catch(() => {});
    }
    entry.ms = Date.now() - wall;
    report.steps.push(entry);
    if (!entry.ok) break; // later steps depend on earlier state
  }

  // ---------- outro + artifacts
  if (RECORD && report.ok) {
    if (cursorOverlay) await cursorOverlay.dispose(); // clean outro card
    const outroStart = now();
    const outroCard = page.screencast.showChapter(OUTRO.title, {
      description: OUTRO.description,
      duration: cfg.outroMs,
    });
    await page.waitForTimeout(cfg.outroMs - CHAPTER_FADE_MS); // cut before the fade-out: the last frame is the card
    const outroEnd = now();
    await capture.finish(outroEnd);
    await outroCard;
    await page.screencast.stop();
    fs.writeFileSync(
      path.join(OUTPUT_DIR, 'steps.json'),
      JSON.stringify(
        {
          video: { ...VIEW, fps: FPS, dpr: DPR },
          intro,
          outro: { start: outroStart, end: outroEnd },
          steps: stepsLog,
          spots,
          navigations,
        },
        null,
        2,
      ),
    );
  } else if (RECORD) {
    if (capture) await capture.finish(now()); // keep the partial capture for diagnosis; no steps.json
    await page.screencast.stop();
  }
  if (!RECORD && report.ok && cfg.useState)
    await context.storageState({ path: STATE }).catch(() => {});
  await browser.close();

  fs.writeFileSync(
    path.join(OUTPUT_DIR, 'report.json'),
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report, null, 2));
  process.exit(report.ok ? 0 : 1);
}

// ---------- capture: Playwright's own screencast file is VP8 at a fixed 1 Mbit/s, which smears text at 2x. Its JPEG
// frames go to ffmpeg instead. Chromium only sends a frame when the page repaints, so each slot of the constant-fps
// output is filled from the wall clock with the latest frame (no drift, no gaps). ultrafast + low crf keeps up in real
// time and is visually lossless; render.py does the final encode.
const CHAPTER_FADE_MS = 350; // Playwright's chapter card fades in/out over 300 ms

function frameCapture(outPath, fps) {
  const ff = spawn(
    'ffmpeg',
    [
      '-y',
      '-v',
      'error',
      '-f',
      'image2pipe',
      '-c:v',
      'mjpeg',
      '-framerate',
      String(fps),
      '-i',
      '-',
      '-c:v',
      'libx264',
      '-preset',
      'ultrafast',
      '-crf',
      '12',
      '-pix_fmt',
      'yuv420p',
      outPath,
    ],
    { stdio: ['pipe', 'inherit', 'inherit'] },
  );
  const done = new Promise((resolve, reject) =>
    ff.on('close', (code) =>
      code ? reject(new Error(`ffmpeg exited ${code}`)) : resolve(),
    ),
  );
  let latest = null,
    start = null,
    written = 0,
    ticker = null;
  const fillTo = (t) => {
    // every slot before t's slot gets the frame that was on screen then
    if (start === null || !latest) return;
    const slot = Math.floor(((t - start) / 1000) * fps);
    while (written < slot) {
      ff.stdin.write(latest);
      written++;
    }
  };
  return {
    onFrame: ({ data }) => {
      fillTo(Date.now());
      latest = data;
    },
    begin(t) {
      start = t;
      ticker = setInterval(() => fillTo(Date.now()), 1000 / fps);
    },
    async finish(tSec) {
      clearInterval(ticker);
      fillTo(start + tSec * 1000);
      if (written === 0 && latest) ff.stdin.write(latest);
      ff.stdin.end();
      await done;
    },
  };
}
