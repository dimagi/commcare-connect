# Recording & Render Snippets

Copy-paste material for `flow.mjs` and `render.py`. Only read this file while actually writing the script or choosing render flags — SKILL.md's Execution Flow is enough for every other step. The runner itself — demo helpers (`d.click/type/select/goto`), cursor, ring, ripple, caption holds and the `steps.json` writer — is `runner.mjs`; its schema and options are in `reference/verify-runner.md`.

## Overlay snippets

Capture, cursor, ring and ripple are handled by `runner.mjs` (rationale in `reference/learnings.md`). For extra visuals use only `page.screencast.showOverlay(html, { duration? })`; inline `<svg>` needs `style="position:static;width:…;height:…"` and text overlays need explicit font/colour. A sticky overlay (no `duration`) returns a disposable (`await tag.dispose()`); with `duration` it awaits, doubling as a pacing pause.

```js
// persistent badge
const badge = await page.screencast.showOverlay(
  `<div style="position:absolute;top:14px;right:14px;padding:6px 12px;background:rgba(17,24,39,.85);color:#fff;border-radius:10px;font:600 15px system-ui">NEW · admin notification</div>`,
);

// element callout from a locator's bounding box
const b = await page.locator(sel).boundingBox();
await page.screencast.showOverlay(
  `
  <div style="position:absolute;left:${b.x - 6}px;top:${b.y - 6}px;width:${
    b.width + 12
  }px;height:${
    b.height + 12
  }px;border:3px solid #ed6a14;border-radius:10px;box-shadow:0 0 0 2px #fff"></div>
  <div style="position:absolute;left:${b.x + b.width / 2}px;top:${
    b.y + b.height + 10
  }px;transform:translateX(-50%);padding:6px 10px;background:#111827;color:#fff;border-radius:8px;font:600 14px system-ui;white-space:nowrap">Both admins are listed here</div>`,
  { duration: 1800 },
);
```

## `render.py` — Post-Process

```bash
R="../venv/bin/python $SKILL/render.py"   # from the task folder, <repo>/.ui-demo/<task>/
$R                       # final.mp4 + poster.png + render.json (frame on, captions on, x264)
$R --voiceover           # mix output/voiceover.json from narrate.py instead of captions (SKILL.md §5a)
$R --fast                # quick preview (VideoToolbox on macOS, libx264 ultrafast elsewhere) while iterating captions
$R --action-speed 1.5    # typing/navigation segments 1.5x; caption holds stay 1x
$R --no-frame            # bare 1920x1200, e.g. for embedding in slides
$R --scale 1             # 1280x800 output, smaller file
$R --spot-dim 0          # no spotlight dim
$R --bg light            # light gradient frame
$R --crf 21 --max-mb 8   # tighter size budget
```

What it does, step by step: the docstring at the top of `render.py`.

Typical cost: a 57 s 2560×1600 recording → ~20 s with libx264 slow at 1920×1200 (5.6 MB with `--action-speed 1.5`). Python only paints frames with an intent, caption or spotlight.

## Manual ffmpeg equivalents (when you need one piece, not the pipeline)

```bash
# probe
ffprobe -v error -show_entries stream=width,height,r_frame_rate:format=duration -of default=noprint_wrappers=1 output/final.mp4

# sample a frame — -ss AFTER -i for frame accuracy; always downscale before viewing (image tokens ∝ pixel area)
ffmpeg -y -i output/final.mp4 -ss 5 -frames:v 1 -vf scale=800:-1 frame.png

# scene-change spread (corruption check — should spread across the whole duration, not cluster early)
ffmpeg -y -i output/raw.mp4 -vf "select='gt(scene,0.01)',showinfo" -vsync vfr -f null - 2>&1 | grep pts_time | sed -E 's/.*pts_time:([0-9.]+).*/\1/'

# flat speed-up of an already-rendered file (captions speed up too)
ffmpeg -y -i output/final.mp4 -filter:v "setpts=PTS/1.5" -an -c:v libx264 -crf 19 -preset slow -tune stillimage -pix_fmt yuv420p -movflags +faststart final_1.5x.mp4

# GIF for a README/PR hero (only for <15 s silent clips; 5–10x larger than mp4)
ffmpeg -y -i output/final.mp4 -vf "fps=12,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" demo.gif

# clip a section (e.g. the one step a reviewer asked about)
ffmpeg -y -ss 12 -to 24 -i output/final.mp4 -c copy clip.mp4
```
