# Verify Runner — script-owned Verify Pass with demo-grade Record Pass

`flow.mjs` is a step runner that owns navigation, waits, assertions, and DOM inspection itself, and returns a **structured JSON report** — Claude reads the report, never per-action page state. The same script records (`--record`); a clean verify run _is_ the verification.

Requires `playwright` ≥1.61 and `@playwright/test` (for standalone polling `expect`).

## STEPS schema

Steps are data. Each step:

- `name` — unique slug; used for `--from=` resume and failure screenshots.
- `run(page, d)` — the action(s). Use the demo helpers `d.click / d.type / d.select / d.goto` for anything the viewer should see; they are plain Playwright in verify mode and polished in record mode (own cursor glide, ring, ripple, human typing). Plain `page.*` calls are fine for setup the viewer never sees — but note that the on-screen cursor only moves through `d.*`.
- `assert(page)` — polling web-first assertion(s) proving the step _worked_. Assert an outcome, not an implementation detail.
- `intent` — ≤42 chars; what is about to happen, shown as a pill during `[actionStart, actionEnd]` (dropped when that window is <0.8 s after speed-up). The runner warns on a captioned step without one.
- `caption: [title, subtitle]` — title ≤42 chars, subtitle ≤60; shown after the step's assertion passes, held for a duration derived from text length. Applied only under `--record`.
- `captionPos` — optional `'top' | 'bottom'`; default: bottom, auto-flipped to top when the clicked target sits in the bottom 28% of the frame (and the step did not navigate), or when the step's `checkpoint` region overlaps the bottom caption band but not the top one.
- `checkpoint` — optional CSS selector; on success (verify mode), `ariaSnapshot()` of that region goes into the report for semantic review. 2–3 per flow, scoped to the main content region. Under `--record` its bounding box is saved as the step's `result`, which keeps the caption off the outcome.
- `failRegion` — optional CSS selector; on failure, `ariaSnapshot()` of that region goes into the report. Defaults to `main, form, [role=main]`.

Runnable examples: `flow.feature-example.mjs` (signed in off camera) and `flow.example.mjs` (login on camera), in this skill's folder.

## Runner

The runner is **`runner.mjs`** in this skill's folder (copy it next to `flow.mjs`; don't paste its code into flows). A flow file is:

```js
import { run, expect } from './runner.mjs';
import * as c from './connect.mjs'; // commcare-connect helpers

const INTRO = { title: '…', description: '…' };
const OUTRO = { title: '…', description: '…' };
const STEPS = [
  /* schema above */
];

await run(
  {
    BASE: c.BASE,
    INTRO,
    OUTRO,
    STEPS,
    preAuth: (p) => c.quickLogin(p, c.USERS.admin),
    firstUrl: c.urls.orgHome,
  },
  import.meta.url,
);
```

`run(config, import.meta.url)` options: `BASE` (required), `INTRO`, `OUTRO`, `STEPS`, `preAuth` (async `(page) => …` run before anything is captured — sign in off camera when login is not the feature; skipped when a saved Verify-Run state exists), `firstUrl` (page loaded before the screencast starts so frame 0 is never blank — the feature's page, not the login page, unless login is the story; default `BASE`), `useState` (reuse auth between Verify Runs; **false** for flows that switch identity), `VIEW` (1280×800), `DPR` (2), `FPS` (30), `maxSteps` (12), `failRegion`, `introMs`/`outroMs` (3000).

What it does: Verify Run = headless, plain actions, assertions, `checkpoint` snapshots, warnings (step count, caption/intent length, missing intent, test-looking data) → `output/report.json`, exit 1 on the first failing step with error/URL/title/`failRegion`/screenshot. Record Pass = headed, `deviceScaleFactor` 2 with a doubled screencast size, macOS-arrow cursor overlay driven by a spring (tension 470, mass 3, friction 70; target = click point from tick one, hard snap + 120 ms landing beat), highlight ring 700 ms, click ripple 450 ms, `pressSequentially` at 60 ms/char, caption holds from text length (20 chars/s, 1.5–7 s), intro card as frame 0 and outro card as the last frame, JPEG frames (`onFrame`, quality 100) piped into ffmpeg libx264 ultrafast crf 12 at a wall-clock-slotted constant 30 fps → `output/raw.mp4` + `output/steps.json`. All overlays go through `page.screencast.showOverlay` (never `page.evaluate`; never `showActions`, which blocks every action for its duration).

The `d` helpers passed to `run(page, d)`: `d.click(page, selectorOrLocator, { highlight })`, `d.hover(page, sel)` (glide onto it without clicking — for tooltips, which a click closes), `d.type(page, sel, text, { delay })`, `d.select(page, sel, option)`, `d.check(page, sel)`, `d.goto(page, url)` (waits for network idle), `d.pause(page, ms)` (record-only beat), `d.badge(page, sel, 'NEW')` (sticky tag beside an element; returns a disposable), `d.now()`.

## `steps.json` (written by the Record Pass)

```json
{
  "video": { "width": 1280, "height": 800, "fps": 30, "dpr": 2 },
  "intro": { "start": 0, "end": 2.6 },
  "spots": [
    {
      "start": 3.3,
      "end": 5.6,
      "box": { "x": 708, "y": 166, "width": 308, "height": 48 }
    }
  ],
  "navigations": [0.4, 8.3],
  "outro": { "start": 61.2, "end": 63.9 },
  "steps": [
    {
      "name": "invite",
      "intent": "Invite Isha as a Member",
      "title": "An invite is pending",
      "subtitle": "Isha has not accepted yet",
      "actionStart": 2.6,
      "actionEnd": 8.1,
      "holdEnd": 10.5,
      "target": { "x": 1012, "y": 372, "width": 88, "height": 40 },
      "navigated": false,
      "captionPos": null,
      "result": null
    }
  ]
}
```

`render.py` reads it to place intents in `[actionStart, actionEnd]` and captions in `[actionEnd, holdEnd]`, to speed up `[actionStart, actionEnd]` when `--action-speed > 1`, to flip a caption to the top when `target` sits in the bottom band or `result` would sit under it, and to dim around each `spots` box (cut at the next `navigations` entry). `video.dpr` tells it the raw frames are 2x the CSS size. **Editing `title`/`subtitle`/`captionPos` here and re-rendering is the way to fix caption wording or placement** — no re-record.

## Iterating on failures

The report gives you: which step, the assertion error, where the browser actually was (`url`/`title`), what the relevant region actually contained (`failRegion`), console errors, failed requests, and `warnings`. Fix the step from that; **do not reach for MCP on the first failure**.

- Resume from the failed step with `--from=<name>` **only when earlier steps have no side effects needed by later ones and auth state covers you** — when in doubt, run the whole flow; verify iterations are cheap.
- Off-camera sign-in (`preAuth`) is saved to `output/state.json` and reused on later Verify Runs, so `preAuth` is skipped. A login _step_ in `STEPS` is never skipped — an already-signed-in session would land it off the login page — so flows that sign in on camera or switch identity must pass `useState: false`. Delete `state.json` if a saved session goes stale.
- Escalate one segment to interactive MCP discovery only after **two** failed fix attempts on the same step, or when the report shows the page structure differs fundamentally from what the source suggested.

## Reading checkpoints and warnings

A clean report is mechanical proof, not semantic proof. Before recording:

1. Read each `checkpoint` snapshot and confirm the _content_ is right: seeded data with the right values, no inline error banners.
2. Read `warnings`. Step-count, caption-length and test-data warnings are demo-quality defects — fix the script or the seed. Only proceed with a warning you have consciously accepted (e.g. a product string that legitimately contains "test").
