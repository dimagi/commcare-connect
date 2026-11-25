#!/usr/bin/env python3
"""render.py — Post-Process for ui-demo. Never touches the browser.

Reads  output/raw.mp4 + output/steps.json (both written by the runner's Record Pass)
Writes output/final.mp4 (+ poster.png — the frame at ~1 s, i.e. the intro card — and render.json)

One decode → Python → encode pass that does, in order:
  1. segment-aware speed: action segments (typing, navigation) are sped up by
     --action-speed; caption/read windows and intro/outro stay 1x. Caption
     timings are remapped through the same time map, so nothing drifts.
  2. 2x → --scale: raw frames are `video.dpr`× the CSS size (2x when recorded
     headed with deviceScaleFactor 2). ffmpeg downscales them to --scale× the
     CSS size (default 1.5: 1280×800 → 1920×1200, Lanczos, multithreaded) while
     decoding, so Python only sees output-size frames and passes frames with
     no overlay straight through untouched.
  3. spotlight: while a d.click/d.type target is active (steps.json `spots`),
     everything except the target is dimmed (--spot-dim, 0 disables), with a
     feathered hole and 0.2 s fades; cut at the next navigation; skipped for
     targets larger than 40% of the frame.
  4. intent + captions: PIL-drawn. During a step's action, a small pill says
     what is about to happen (step.intent); after it, the card (Helvetica Neue
     Bold title / Regular subtitle on a dark translucent box with a brand accent
     bar) says what happened. Both sized from frame height, fade at the edges,
     bottom-centre inside a 5% safe margin, or top when step.captionPos ==
     'top', when the clicked target sits in the bottom band and the step did
     not navigate, or when the step's `result` box would sit under a bottom
     caption but not a top one. At most two lines per block, 80% frame width.
  5. device frame (unless --no-frame): gradient background, 5% padding,
     rounded corners, soft shadow — bg/mask rendered once, not per frame.
  6. encode: libx264 crf 19 slow, tune stillimage, yuv420p, +faststart, g=30.
     --fast swaps in h264_videotoolbox on macOS, libx264 ultrafast elsewhere (and bilinear resampling) for previews.
  7. budget: if final.mp4 > --max-mb (default 10, GitHub's free-plan cap), one
     re-encode at crf+3.
  8. voiceover (--voiceover, after narrate.py): output/voiceover.json's lines
     are placed at their anchors in output time, one after another, and any
     line that would outrun its window holds the settled frame (`holdAt`)
     until it finishes — speech is never cut or sped up. The intro card is held
     or trimmed so the first step starts INTRO_GAP_S after the intro line ends,
     however long the recorded card ran. The voice replaces intent pills and
     caption cards; the spotlight stays. Mixed to AAC.

This file is the CLI and the pipeline; each step lives beside it: render_timing.py (time map, frame repeats),
render_captions.py, render_spotlight.py, render_voiceover.py, render_ffmpeg.py (probe, encode, device frame, budget).

Usage:
  python3 render.py [--task DIR] [--out final.mp4] [--action-speed 1.0]
                    [--scale 1.5] [--spot-dim 0.4] [--no-frame] [--bg dark|light]
                    [--fps N] [--crf 19] [--fast] [--max-mb 10] [--no-captions] [--voiceover]
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from PIL import Image
from render_captions import CaptionPainter, build_captions
from render_ffmpeg import (
    BG_COLORS,
    decode_command,
    device_frame,
    encode_command,
    fit_size_budget,
    probe,
    write_poster,
)
from render_spotlight import Spotlight, until_next_page
from render_timing import TimeMap, frame_repeats
from render_voiceover import mix_voiceover, schedule_voiceover


def main():
    args = parse_args()
    plan = plan_render(args)
    written = render_video(plan)
    size_mb = fit_size_budget(plan.final, args)
    write_poster(plan.final, plan.out_dir / "poster.png", min(1.0, max(0.0, written / args.fps - 0.1)))
    for p in ("_mask.png", "_shadow.png", "_bg.png"):
        (plan.out_dir / p).unlink(missing_ok=True)
    write_summary(plan, written, size_mb)


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default=".", help="task folder holding output/")
    ap.add_argument("--out", default="final.mp4")
    ap.add_argument("--action-speed", type=float, default=1.0, help=">1 speeds up typing/navigation segments")
    ap.add_argument("--no-frame", action="store_true")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--bg", choices=BG_COLORS, default="dark")
    ap.add_argument("--fps", type=int, default=None, help="default: the recording's fps")
    ap.add_argument("--scale", type=float, default=1.5, help="output size as a multiple of the CSS viewport (≤ dpr)")
    ap.add_argument(
        "--spot-dim", type=float, default=0.4, help="spotlight: how much to dim around the target; 0 = off"
    )
    ap.add_argument("--crf", type=int, default=19)
    ap.add_argument(
        "--fast",
        action="store_true",
        help="preview encode (h264_videotoolbox on macOS, libx264 ultrafast elsewhere) + bilinear downscale",
    )
    ap.add_argument("--max-mb", type=float, default=10.0)
    ap.add_argument("--voiceover", action="store_true", help="mix output/voiceover.json (narrate.py); drops captions")
    return ap.parse_args()


class Canvas(NamedTuple):
    """The output frame: `k` output px per CSS px, from a capture of `src_w`×`src_h` at `dpr`× the viewport."""

    width: int
    height: int
    k: float
    css_h: int
    src_w: int
    src_h: int
    dpr: float


def fit_canvas(raw, src, video, scale):
    """Raw frames are dpr× the CSS viewport (2x capture); output is --scale× it, never upscaled past the source."""
    SW, SH = src["width"], src["height"]
    css_w, css_h = int(video.get("width") or SW), int(video.get("height") or SH)
    dpr = SW / css_w
    if abs(dpr - round(dpr)) > 0.01 or abs(SH / css_h - dpr) > 0.01:
        sys.exit(f"{raw.name} {SW}x{SH} is not an integer multiple of the CSS viewport {css_w}x{css_h}")
    k = min(scale, dpr)
    return Canvas(round(css_w * k / 2) * 2, round(css_h * k / 2) * 2, k, css_h, SW, SH, dpr)


@dataclass
class RenderPlan:
    """Everything the render needs, worked out before the first frame."""

    args: argparse.Namespace
    out_dir: Path
    raw: Path
    final: Path
    src: dict
    canvas: Canvas
    tmap: TimeMap
    voice: list
    caps: list
    spotlight: Spotlight
    pad: int
    frame: tuple | None


def plan_render(args):
    out_dir = Path(args.task) / "output"
    raw = out_dir / "raw.mp4"
    if not raw.exists():
        sys.exit("No output/raw.mp4 found — run `node flow.mjs --record` first")
    src = probe(raw)
    steps, spots, navigations, video, intro = load_steps(out_dir)
    canvas = fit_canvas(raw, src, video, args.scale)
    args.fps = args.fps or int(video.get("fps") or 25)
    tmap = TimeMap(steps, args.action_speed)
    voice = schedule_voiceover(out_dir, tmap, intro) if args.voiceover else []  # adds holds to tmap: before the rest
    caps = [] if args.no_captions or args.voiceover else build_captions(steps, spots, tmap, canvas.css_h)
    spotlight = Spotlight(until_next_page(spots, navigations), tmap, canvas, args.spot_dim)
    pad, frame = device_frame(out_dir, canvas, args)
    return RenderPlan(args, out_dir, raw, out_dir / args.out, src, canvas, tmap, voice, caps, spotlight, pad, frame)


def render_video(plan):
    """Encodes the capture, retimed and painted, into plan.final and mixes in the voiceover; returns frames written."""
    args, canvas = plan.args, plan.canvas
    written = render_frames(
        decode_command(plan.raw, canvas, args.fast),
        encode_command(args, plan.final, canvas, plan.frame),
        FrameComposer(canvas, plan.caps, plan.spotlight),
        frame_repeats(plan.tmap, plan.src["fps"], args.fps),
    )
    mix_voiceover(plan.out_dir, plan.final, plan.voice)
    return written


def write_summary(plan, written, size_mb):
    args, canvas, pad = plan.args, plan.canvas, plan.pad
    summary = {
        "final": str(plan.final),
        "size_mb": round(size_mb, 2),
        "frames": written,
        "duration_s": round(written / args.fps, 2),
        "source_duration_s": round(plan.src["duration"], 2),
        "source": f"{canvas.src_w}x{canvas.src_h} (dpr {canvas.dpr:g})",
        "canvas": f"{canvas.width + 2 * pad}x{canvas.height + 2 * pad}",
        "action_speed": args.action_speed,
        "sped_segments": len(plan.tmap.segs),
        "captions": sum(c["kind"] == "caption" for c in plan.caps),
        "intents": sum(c["kind"] == "intent" for c in plan.caps),
        "spots": len(plan.spotlight.items),
        "voice_lines": len(plan.voice),
        "held_s": round(sum(sec for _, sec in plan.tmap.freezes), 2),
        "cut_s": round(sum(sec for _, sec in plan.tmap.cuts), 2),
        "over_budget": size_mb > args.max_mb,
    }
    (plan.out_dir / "render.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


class FrameComposer:
    """Paints the captions and the spotlight active at output time t onto a decoded frame. Most frames have
    neither and pass through untouched."""

    def __init__(self, canvas, caps, spotlight):
        self.size = (canvas.width, canvas.height)
        self.frame_bytes = canvas.width * canvas.height * 3
        self.caps = caps
        self.spotlight = spotlight
        self.painter = CaptionPainter(*self.size)

    def __call__(self, buf, t):
        active = [c for c in self.caps if c["start"] <= t <= c["end"]]
        spot = self.spotlight.active(t)
        if not active and spot is None:
            return buf
        img = Image.frombytes("RGB", self.size, buf)
        if spot:
            img = self.spotlight.paint(img, spot, t)
        for cap in active:
            self.painter.paint(img, cap, t)
        return img.tobytes()


def render_frames(decode_cmd, enc_cmd, compose, repeats):
    """Pipes each decoded source frame through `compose` into the encoder as many times as `repeats` says (see
    frame_repeats). Returns the number of frames written."""
    decode = subprocess.Popen(decode_cmd, stdout=subprocess.PIPE)
    encode = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE)
    written = 0
    for buf, (t_out, count) in zip(read_frames(decode.stdout, compose.frame_bytes), repeats):
        frame = compose(buf, t_out) if count else None
        for _ in range(count):
            encode.stdin.write(frame)
        written += count
    encode.stdin.close()
    decode.wait()
    encode.wait()
    if encode.returncode:
        sys.exit(f"encode failed ({encode.returncode})")
    return written


def read_frames(stream, frame_bytes):
    while len(buf := stream.read(frame_bytes)) == frame_bytes:
        yield buf


def load_steps(out_dir):
    """steps.json (written by the runner) → (steps, spots, navigations, video meta, intro card)."""
    steps_path = out_dir / "steps.json"
    if not steps_path.exists():
        sys.exit("No output/steps.json found — run `node flow.mjs --record` first")
    data = json.loads(steps_path.read_text())
    return (
        data.get("steps", []),
        data.get("spots", []),
        data.get("navigations", []),
        data.get("video", {}),
        data.get("intro"),
    )


if __name__ == "__main__":
    main()
