---
name: ui-demo
description: 'Verify a commcare-connect UI flow and produce a polished, share-ready demo video (captions or a voiceover with --voiceover, cursor, intro/outro, device frame, mp4). A script-driven Verify Run proves the flow works before a single headed Record Pass. Use only when the user explicitly asks for a demo/recording or names this skill.'
---

# UI Demo — verified flow, demo-grade recording

Goal: a verified UI recording a PM or stakeholder can watch once on Slack/Jira/GitHub and understand, with minimal retries, runtime and tokens.

Core idea: **the script owns browser state, not the conversation.** `flow.mjs` performs navigation, waits, assertions, retries and DOM inspection internally and returns a structured JSON report; context receives reports and small checkpoint snapshots, never per-action page state. Playwright MCP is a discovery fallback only. Every default below (cursor, spotlight, captions, overlays, 2x capture) is measured — see `reference/learnings.md`. Polish is on by default under `--record`; the Verify Run stays plain and fast.

## Passes

- **Verify Run** — `node flow.mjs`: headless, assertion-instrumented run of the script that will record; emits `output/report.json`. Iterate until clean.
- **Record Pass** — `node flow.mjs --record`: headed, 2x; produces `raw.mp4` + `steps.json`.
- **Post-Process** — `render.py` on existing artifacts (speed, captions, frame, encode); never touches the browser.
- **Voiceover** (only with `--voiceover`) — `narrate.py` voices each step's `say` lines; `render.py --voiceover` mixes them in place of captions.
- **Spot-Check** — quick look at `final.mp4`; not a re-investigation.

**Arguments.** `--voiceover` in the skill's arguments (or a request for narration or a voiceover) makes it a narrated demo: write `say` lines while drafting (§2) and run §5a. Without it, captions as usual. `--voiceover <task>` on a task that is already recorded needs no new recording: add `say` to its `output/steps.json` (§5a) and run only §5a and §6.

## Golden Rules

1. **Never record an unverified workflow.** Record only after a clean Verify Run report _and_ a checkpoint review (mechanical + semantic proof).
2. **Draft from source, verify by script.** Read templates/views, write the complete `flow.mjs` (every step with assertion, caption, checkpoints), iterate the Verify Run. Playwright MCP only when source can't determine a segment's selectors, or a step failed two fixes and the report's failure context doesn't explain why — scoped to that segment. It never blocks the Record Pass (`reference/troubleshooting.md`).
3. **Context receives reports, not pages.** Debug from the report's failure context; judge semantics from checkpoint snapshots.
4. **Record once, render freely.** `raw.mp4` + `steps.json` are a durable pair: never delete them, never regenerate just to restyle, overwrite only together and only when the workflow under test changed. Caption wording/position, speed, frame and encode changes are `steps.json` edits or `render.py` flags + a re-render.
5. **It has to read as a demo, not a test.** Realistic names/orgs/emails, no debug toolbar, 6–10 steps (the runner warns past 12; split into two videos), every step captioned in plain language a PM understands.

Capture is `page.screencast` — never MCP's `browser_video_*` tools (tool round-trips bake into the capture as dead air). Overlays only via `page.screencast.showOverlay/showChapter`, never `page.evaluate` DOM injection; real HTTP navigation only, never `data:` URLs (both silently missing from capture).

## Setup (once per task)

Run `preflight.sh` first (Node 20+, Python 3 venv, ffmpeg with libx264, the repo's `.venv`, a display for the headed Record Pass); it prints what to install. Playwright ≥1.61 is required (`showOverlay`) and is installed by the block below. **On Linux**, Chromium also needs its system libraries once: `sudo npx playwright install-deps chromium` (needs sudo, so the user runs it; preflight warns when they look missing). Captions there use DejaVu Sans, as Helvetica Neue is macOS-only.

Kick off `$SKILL/serve.sh` and `$SKILL/reseed.sh` in the background _before_ reading any code, and draft `flow.mjs` while they run. `curl` readiness right before the first Verify Run.

The workspace is persistent at `<repo>/.ui-demo/`, git-ignored by its own `.gitignore` (`*`): shared `node_modules/` + `venv/` at the root, one subfolder per task (ticket ID verbatim, else a short kebab-case slug) holding `flow.mjs`, the copied `runner.mjs` + `connect.mjs`, and `output/` (`raw.mp4`, `steps.json` durable; `report.json`, `state.json`, `mail/`, `final.mp4`, `poster.png`, `render.json` derived). Every other script runs in place from `$SKILL`.

```bash
REPO=$(git rev-parse --show-toplevel)
SKILL="$REPO/.claude/skills/ui-demo"
RECORD_DIR="$REPO/.ui-demo"
TASK_DIR="$RECORD_DIR/<task-id-or-slug>"
"$SKILL/preflight.sh" || { echo "fix the ✗ lines above, then re-run setup"; exit 1; }   # add --voiceover to also check uv
mkdir -p "$RECORD_DIR" && [ -f "$RECORD_DIR/.gitignore" ] || echo '*' > "$RECORD_DIR/.gitignore"
if [ ! -d "$RECORD_DIR/node_modules" ]; then
  cd "$RECORD_DIR" && echo '{ "name": "ui-demo", "private": true }' > package.json   # not `npm init -y`: ".ui-demo" is no valid package name
  npm install playwright @playwright/test
  python3 -m venv venv && source venv/bin/activate && pip install --quiet pillow
fi
(cd "$RECORD_DIR" && npx playwright install chromium)   # no-op when present
mkdir -p "$TASK_DIR/output" && cd "$TASK_DIR"
cp "$SKILL"/{runner.mjs,connect.mjs} .   # copied, not run in place: Node resolves `playwright` from the file's own folder
[ -f flow.mjs ] || cp "$SKILL/flow.feature-example.mjs" flow.mjs   # signed in off camera; flow.example.mjs when signing in is the story
"$SKILL/serve.sh" 8010 && "$SKILL/reseed.sh"   # runserver (DEBUG off, email → demo inbox), realistic data
```

**Recording a branch you don't have checked out.** `serve.sh` and `reseed.sh` run the code of the checkout `$SKILL` sits in, so serve the branch from a throwaway worktree and keep the task folder under `$REPO/.ui-demo/`:

```bash
WT="<scratchpad>/<task>-wt"
git worktree add --detach "$WT" <branch>
[ -d "$WT/.claude/skills/ui-demo" ] || git -C "$WT" checkout <ref-with-the-skill> -- .claude/skills/ui-demo   # branch older than the skill
for f in .venv node_modules .env; do ln -s "$REPO/$f" "$WT/$f"; done   # fine while its lockfiles match yours
(cd "$WT" && npm run dev)   # the branch's own JS/CSS bundles
SKILL="$WT/.claude/skills/ui-demo"   # then setup as above, from $TASK_DIR
```

The worktree shares your DB. Compare its `./manage.py showmigrations` with the DB first: when the branch adds migrations, or your DB has applied ones the branch lacks, give it a DB of its own with `scripts/setup-worktree.sh` instead. Remove it after delivery with `git worktree remove --force "$WT"`.

**Reseed before every Record Pass** — a Verify Run mutates data (accepts invites, creates users). Touch only the active task folder; never remove shared tooling, other task folders, or the active durable pair.

## 1. Seed data that reads as real

`seed.py` provides the **Child Health Campaign** workspace and people whose name and email say their role (Asha Admin, Isha Invitee, …), so no legend is needed. Who's who, the other workspaces and the password: `reference/connect.md`.

Feature data (programs, opportunities, workers, visits, payments, invoices) is built through the app by a simulation scenario (`commcare_connect/simulation/README.md`) wherever it has steps for the area: `$SKILL/reseed.sh <scenario>` reseeds and then rebuilds that scenario with Asha as admin, so every take starts from the same state. The standard scenarios name their orgs `Sim <Scenario> PM`/`NM`, which reads as test data on camera; for a recording, write a personal scenario in `commcare_connect/simulation/scenarios/local/` (git-ignored) from the existing steps with realistic names (programme names in `reference/connect.md`). For an area with no simulation steps yet (solicitations, tasks, microplanning, audit, reports), write a task-local `seed_extra.py` instead: `reseed.sh` runs it from the task folder after the seed and any scenario, so it must delete its own rows before recreating them.

Never `test123`, `example.com`, `uitest.local` — viewers translate "demo/test" into "fake"; the runner warns when such tokens are visible in checkpoints.

## 2. Draft `flow.mjs` from source

`import { run, expect } from './runner.mjs'`, the Connect helpers (`import * as c from './connect.mjs'`), then `INTRO`, `OUTRO`, `STEPS`, `await run({...}, import.meta.url)`. Schema in `reference/verify-runner.md`; working examples in this skill's folder (`flow.feature-example.mjs` signed in off camera, `flow.example.mjs` login on camera).

- **Start where the feature starts.** Sign in off camera with `preAuth: (p) => c.quickLogin(p, c.USERS.admin)` and open the feature page as `firstUrl`. Put login in `STEPS` only when signing in is the story (auth, invites, permissions).
- **Merge setup-only clicks** into one step; the viewer needs outcomes, not every click.
- Every step has `run(page, d)` using `d.click/type/select/goto` and an `assert` that proves the _outcome_, not just that an element exists.
- `intent` — ≤42 chars, what is about to happen, shown as a pill during the action ("Invite Isha as a Member"). Dropped when the action is under 0.8 s; the runner warns on a captioned step without one.
- `caption: [title, subtitle]` — title ≤42 chars, plain language ("Invite sent to Isha", not "Invitation record created"). Subtitle = _why it matters to the user_, not a second description of the screen ("Both will hear when someone joins"). Hold time is derived from text length.
- A `checkpoint` region on each page whose outcome matters (2–3 per flow), scoped to the main content area; the renderer flips captions off the clicked target and off the checkpoint. `captionPos: 'top'` only to override.
- `INTRO`/`OUTRO` follow one arc — **problem → walkthrough → result**. Intro names the need ("Know when your invite is accepted"), description says what was missing before (for a bug fix, the broken behaviour). Outro states what is now true ("Isha joined, and both admins know"), not "Done".
- **Emails show up on their own.** The demo server sends no real email: it keeps each one for a demo inbox at `/demo-inbox/`. After a step that sent email, the runner opens the first recipient's inbox, clicks the new email (its subject, then the email exactly as sent, in an iframe; no sender), holds it long enough to read and goes back to the page the step ended on — in the Verify Run too, so the return trip is verified. Nothing to write. `showEmail: false` on a step skips it, e.g. when the story continues from the email: open it in the next step with `d.openEmail(p, to)` and click its link, which opens in the whole window: `d.click(p, p.frameLocator('iframe[title="Email"]').getByRole('link', { name: 'Accept the invitation' }))`. With `--voiceover`, mention the email in the step's `outcome` line; the inbox has no line of its own.
- **With `--voiceover`: `say` on every step and on `INTRO`/`OUTRO`** — `say: { intent: '…', outcome: '…' }` on a step, a plain string on a card. The words of a presenter giving the demo live, first person, talking while they click and then reacting to the screen ("Let me start a draft, and I'll set the max budget below the min on purpose" → "And it's caught on save."), not the caption read aloud. One or two short sentences per line; spell numbers as words ("sixty percent"); end each line with a full stop (a trailing comma voices badly). Size the intent line to the action (≈2.7 words/s): a long form fill can carry a sentence of context, a single click gets three words or `null`.
- Selectors come from code; the Verify Run confirms them. No fixed sleeps.

## 3. Verify Run loop

`node flow.mjs` — headless, no video, prints `report.json`.

- **On failure:** fix from the report's assertion error, URL/title, `ariaSnapshot`, console errors and failed requests, then re-run. `preAuth` is reused via `state.json` (`useState: false` for flows that sign in on camera or switch identity); `--from=<step>` resumes only when earlier steps have no needed side effects.
- **On a clean report:** start `$SKILL/reseed.sh` (with the task's scenario, if any) in the background, then read each `checkpoint` snapshot (right data, right values, no error banners) and `report.warnings` — step-count, caption-length and test-data warnings are demo-quality defects; fix before recording. If you fix and re-verify, reseed again.

Exit criteria: clean report + checkpoints reviewed + no unexpected console errors/failed requests + no unaccepted warnings.

## 4. Record Pass

`node flow.mjs --record` in the background (needs no supervision; recording is real time). It starts from a fresh non-`storageState` context so login is actually shown. Meanwhile draft the delivery message and pick render flags.

## 5. Post-Process

```bash
../venv/bin/python "$SKILL/render.py"            # final.mp4 + poster.png + render.json, from ./output
../venv/bin/python "$SKILL/render.py" --fast     # preview encode while iterating captions
```

Defaults: 1920×1200, spotlight on, intent pills + captions, dark device frame, 30 fps, `libx264 -crf 19`, 10 MB budget with one automatic `crf+3` retry. Flags and details: `reference/recording-snippets.md`. Caption wording lives in `steps.json`.

## 5a. Voiceover (only with `--voiceover`)

```bash
"$SKILL/preflight.sh" --voiceover
uv run "$SKILL/narrate.py"                              # output/vo/*.wav + output/voiceover.json
../venv/bin/python "$SKILL/render.py" --voiceover       # same flags as §5; captions and intent pills off
```

- `narrate.py` is a uv inline script: Chatterbox, on an NVIDIA GPU (CUDA) or Apple's GPU (MPS) when there is one, else the CPU — much slower, so a laptop without a GPU takes a while. Defaults: built-in voice, exaggeration 0.5, cfg 0.5, **tempo 0.9**, a little slower than speech so a viewer can follow the clicks. `--voice ref.wav` clones a voice from 5–9 s of clean speech.
- **First run:** uv installs its packages into a cached environment of their own (not the repo's `.venv`, not `.ui-demo/venv`) and Chatterbox downloads its weights (~3.2 GB, to `~/.cache/huggingface`); later runs reuse both. On x86_64 Linux, torch is the CUDA build and adds 13 NVIDIA packages, a few GB more. Works on Apple Silicon and Linux; an Intel Mac has no torch 2.6 build, and `preflight.sh --voiceover` stops there.
- Clips are cached by text + settings, so editing one `say` in `steps.json` re-voices only that line. Chatterbox is stochastic: re-take a line that sounds off by editing it, or all of them with `--fresh`.
- Timing: each line starts at its anchor (card start, `actionStart`, `actionEnd`) or 0.35 s after the previous line ends. When a step's lines outrun its hold, `render.py` holds the step's settled frame until they finish + 0.5 s (`render.json` `held_s`); the outro card holds the same way. The intro card is fitted to its line instead, held or trimmed (`cut_s`) so the first step starts 0.6 s after the intro line ends.
- A step without `say` falls back to its caption text with a warning — fix it; captions read aloud don't sound like a demo.
- **Already recorded:** add `say` to each step in `output/steps.json`, and `title`/`description`/`say` to its `intro`/`outro` (recordings made before voiceover support lack them). Post-Process only, no new recording.

## 6. Spot-Check

Check `render.json` (`duration_s` plausible vs. Record Pass wall-clock, `over_budget: false`; with a voiceover also `voice_lines` matching `voiceover.json`, `held_s` small next to the duration — large means lines too long for their steps — and `ffprobe` showing an `aac` stream), then sample the first second (intro card visible), the last second (outro card) and, for a step with an `email` window in `steps.json`, a frame inside it (the opened email): `ffmpeg -ss <t> -i output/final.mp4 -frames:v 1 -vf scale=800:-1 frame.png` — always downscale. Each frame shows the expected page, a legible caption not covering its target, and the frame. If a caption covers content, set `captionPos` in `steps.json` and re-render. If something is wrong (duration far off, blank/stale frame), see `reference/troubleshooting.md`. Never deliver an unverified recording.

## 7. Cleanup & deliver

After the Spot-Check passes: delete seeded/test rows the flow created, `output/fail-*.png`, servers started for this recording (`$SKILL/serve.sh stop <port>`, confirm down with `curl`), and stray `.playwright-mcp/`. Keep shared tooling and every task's `flow.mjs` and durable pair. `git status --porcelain` should show nothing from `.ui-demo/`.

Deliver `final.mp4` (plays inline on Slack, Jira, Confluence, GitHub), plus `poster.png` where a still is enough. Mention duration and step count in one line.

## Reference

- `reference/verify-runner.md` — **the core**: STEPS schema, demo helpers, `run()` options, `steps.json`, report + warnings, `--from` resume, `storageState` reuse.
- `reference/recording-snippets.md` — overlay snippets, `render.py` options, ffmpeg one-liners.
- `reference/troubleshooting.md` — MCP unavailable, blocked verification, corrupted recording, TomSelect, zsh gotcha.
- `reference/learnings.md` — measured findings behind the rules.
- `reference/connect.md` — the seeded people, `connect.mjs` helpers, commcare-connect gotchas.
- Skill folder — `connect.mjs`, `seed.py`, `record_settings.py`, `demo_mail.py` + `demo_urls.py` + `templates/` (the demo inbox), `serve.sh` (port check, DEBUG off, static files without collectstatic, readiness wait), `reseed.sh [scenario]`, `render.py` + its `render_*.py` modules (Post-Process), `narrate.py` (voiceover), example flows.
