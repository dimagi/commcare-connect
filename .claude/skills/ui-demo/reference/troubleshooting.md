# Troubleshooting & Failure Recovery

Read this only when something is broken. The happy path lives in SKILL.md.

## Playwright MCP unavailable or profile locked

MCP is only the discovery fallback; the Verify Run and Record Pass run standalone `flow.mjs`. If a step keeps failing where you'd have escalated to MCP, tell the user discovery is degraded rather than guessing blindly.

- `claude mcp list` showing "Connected" does not mean its tools work in _this_ session — resolve `browser_navigate` via `ToolSearch` first. If it doesn't resolve, start a fresh session (not `--resume`). If not connected at all: `claude mcp add playwright -s local -- npx @playwright/mcp@latest --browser=chrome --caps=devtools --image-responses=omit`. Don't switch to Chrome DevTools MCP.
- **Never kill processes to free a locked profile** without confirming which session owns them (identical `playwright-mcp` command lines can include this session's live connection). Ask the user, or use `--isolated`.

## Verification blocked entirely

In ui-demo, verification is the assertion-instrumented Verify Run (`node flow.mjs`) — a real browser driving the real app — so MCP being down does not block verification. What counts as blocked: the Verify Run itself can't execute (Playwright/chromium broken in the workspace, server won't start). In that case stop and tell the user — **do not substitute non-browser verification** (curl, reading page source, static analysis) and call it done: curl can't see client-side JS, AJAX-loaded content, form validation, or auth-gated state, so a flow "verified" that way isn't actually verified. Never record on the back of a substitute; never declare verified without a clean report _and_ reviewed checkpoints.

## Spot-check failed / recording looks corrupted

Escalate to the full scene-change spread check (`reference/recording-snippets.md`, "Manual ffmpeg equivalents"):

- Scene changes must spread across the whole duration, not cluster near the start — clustering in the first 20% means most of the flow got dropped.
- Sample a frame at the suspicious timestamp (`-ss` after `-i` for frame accuracy, always downscale to 800px).

Find where the recording broke, fix the script, confirm with a clean Verify Run, then re-record **once**. Don't loop record→check→tweak→record hoping to converge. Don't run this full check by default — it decodes the entire file for a problem the quick spot-check will already have flagged 99% of the time.

## Caption covers the thing it describes / wrong wording

Not a recording problem. Edit `output/steps.json` — set that step's `captionPos` to `"top"` or `"bottom"`, or fix `title`/`subtitle` — and re-run `render.py`. Never re-record for caption placement or wording.

## Highlight ring or ripple missing from the video

Both go through `page.screencast.showOverlay()`, which is captured (verified). If a ring is missing: the click was made with plain `page.click` instead of `d.click`, or `highlight: false` was passed. If _everything_ overlay-ish is missing, Playwright is <1.61 — check `node -e "console.log(require('playwright/package.json').version)"` in `<repo>/.ui-demo`.

## First step lands on the login page although `preAuth` is set

Usual cause: **something reseeded or changed the demo users while you were recording** — e.g. a second recording running against the same local DB. Django derives the session auth hash from the password hash, so rotating a password logs out every live session of that user. `seed.py` only sets the password when it doesn't already match, and the runner re-runs `preAuth` once (still off camera) if `firstUrl` lands on login. If it recurs, check for another dev server or recording on the same DB (`lsof -iTCP -sTCP:LISTEN | grep runserver`) and don't reseed while it runs.

## Recording is 1280×800, not 2560×1600

The Record Pass ran headless. 2x capture only works headed (`headless: !RECORD` in `runner.mjs`). Also check `DPR = 2` and that `screencast.start` size is `VIEW × DPR`. `render.py` still works on a 1x source (`--scale` is capped at the source's dpr); text just looks softer.

## `render.py` fails or is slow

- `ffmpeg` not on PATH or the venv lacks Pillow: `<repo>/.ui-demo/venv/bin/pip install pillow`.
- Encode is slow: `--fast` for iteration (VideoToolbox on macOS, libx264 ultrafast elsewhere), keep the default encode for the deliverable.
- Output far larger than expected: check `render.json` `over_budget`; pass `--crf 22` or `--no-frame`. Never fix size by dropping fps.
- Captions are drawn in Python (Pillow), not with ffmpeg's `drawtext`/`ass` filters, because many ffmpeg builds — Homebrew's included — ship without libass/libfreetype. Any ffmpeg with libx264 works; don't switch to a filter-based caption path without testing it.

Note the failure mode by driver: `page.screencast`'s continuous capture doesn't sample or drop frames silently. MCP-driven recording's failure mode isn't dropped frames either — it's dead-air padding from tool-call round trips (see `reference/learnings.md`).

## TomSelect picker: step times out, or passes for the wrong reason

Use `c.pickTomSelect()` from `connect.mjs` rather than rewriting this — all four points are verified.

| Symptom                                                                              | Cause                                                                                                                                                                                        | Fix                                                                                                      |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `locator.click` times out on the control, only under `--record`                      | TomSelect's real input is `size="1"`; the `.ts-wrapper` intercepts pointer events. Headless `el.click()` does not care, the record cursor clicks real coordinates                            | Click `.ts-wrapper:has(#<id>-ts-control) .ts-control`                                                    |
| Typing does nothing: input stays empty, dropdown shows the unfiltered list           | TomSelect ignores keystrokes until it registers focus itself; a click arriving from another focused field does not always trigger it                                                         | `page.evaluate((s) => document.querySelector(s).tomselect.focus(), '#<id>')` before typing               |
| `.option:text-is("X")` times out — but the failure screenshot clearly shows that row | When the search filters, TomSelect wraps the match in `<span class="highlight">`. Playwright's `:text-is()` matches an element's **own** text node only, so the `.option` div stops matching | Match `.option[data-value="X"]`                                                                          |
| Step goes green headless, dies headed — "it works in Verify but not in Record"       | **False green.** `:text-is()` matches only while typing is _broken_ (unfiltered list = plain text rows). Fixing focus is what "breaks" the selector                                          | Assert the list filtered (`toHaveCount(1)`) before clicking the row, so a dropped keystroke fails loudly |

The general lesson beyond TomSelect: when a step passes headless and fails headed, suspect that
the two runs are failing _differently_ rather than that the browser behaves differently. Dump the
DOM at the moment of the action and compare locator counts side by side — one `page.evaluate`
dump answered what six selector guesses could not.

## Shell: a record or verify pass that never ran

`rm -f output/fail-*.png` with no matches **aborts the whole `&&` chain under zsh** (`nomatch`),
so the `node flow.mjs --record` after it silently never starts — and the follow-up `node -e`
then reads the _previous_ `report.json` and prints a convincing `ok: true`. Use
`find output -name 'fail-*.png' -delete`, and confirm a pass really ran by checking that
`raw.mp4`/`steps.json` mtimes moved, not just that the report says ok.
