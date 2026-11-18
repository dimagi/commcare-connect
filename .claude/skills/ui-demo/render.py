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

Usage:
  python3 render.py [--task DIR] [--out final.mp4] [--action-speed 1.0]
                    [--scale 1.5] [--spot-dim 0.4] [--no-frame] [--bg dark|light]
                    [--fps N] [--crf 19] [--fast] [--max-mb 10] [--no-captions] [--voiceover]
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FADE_S = 0.2
MIN_WINDOW_S = 0.8  # an intent/caption shorter than this (after speed-up) is dropped, not flashed
SAFE = 0.05  # safe margin as a fraction of the frame
BOTTOM_BAND = 0.72  # a clicked target lower than this flips the caption to the top
CAPTION_BAND = {"bottom": (0.78, 0.95), "top": (0.09, 0.26)}  # vertical extent of a two-line card, fraction of H
CARD_BG = (17, 24, 39)  # slate-900
ACCENT = (237, 106, 20)  # Connect orange
BG_COLORS = {"dark": ("0x1e293b", "0x334155"), "light": ("0xe2e8f0", "0xcbd5e1")}
LINE_GAP_S = 0.35  # silence between two voiceover lines
SETTLE_S = 0.5  # the frame stays still this long after a step's last line before the next action starts
INTRO_GAP_S = 0.6  # intro line end → first step start, the card's fade-out included
CARD_FADE_S = 0.4  # the tail of the intro capture that is the card fading out (Playwright: 300 ms) — never trimmed
# (path, face index) — first that exists wins. Helvetica Neue renders visibly better than Arial (learnings.md).
FONT_CANDIDATES = {
    "bold": [
        ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ],
    "regular": [
        ("/System/Library/Fonts/HelveticaNeue.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ],
}

# --------------------------------------------------------------------------- inputs


def probe(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    info = json.loads(out)
    s = info["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return {
        "width": s["width"],
        "height": s["height"],
        "fps": float(num) / float(den),
        "duration": float(info["format"]["duration"]),
    }


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


# --------------------------------------------------------------------------- time map


class TimeMap:
    """Piecewise-linear source→output time map. Action segments run at `speed`, everything else 1x, each freeze
    (source t, seconds) holds the frame at t for that long, and each cut (source t, seconds) drops that much 1x source
    from t on — the render loop skips frames whose output time does not advance."""

    def __init__(self, steps, speed, min_len=1.0):
        segs = []
        if speed > 1.0:
            for s in steps:
                a, b = s.get("actionStart"), s.get("actionEnd")
                if a is not None and b is not None and b - a >= min_len:
                    segs.append((a, b))
        self.segs = sorted(segs)
        self.speed = speed
        self.freezes = []
        self.cuts = []

    def __call__(self, t):
        held = sum(sec for at, sec in self.freezes if at < t)
        cut = sum(min(max(t - at, 0.0), sec) for at, sec in self.cuts)
        return self.base(t) + held - cut

    def base(self, t):
        out, cursor = 0.0, 0.0
        for a, b in self.segs:
            if t <= a:
                break
            out += a - cursor
            out += (min(t, b) - a) / self.speed
            cursor = min(t, b)
            if t <= b:
                return out
        return out + (t - cursor)


# --------------------------------------------------------------------------- captions


def load_font(kind, size):
    for p, idx in FONT_CANDIDATES[kind]:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size, index=idx)
            except OSError:
                continue
    return ImageFont.load_default(size)  # Pillow's bundled scalable font (Pillow 10.1+)


def wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:2]  # never more than two lines per block


class CaptionPainter:
    def __init__(self, W, H):
        self.W, self.H = W, H
        self.title_font = load_font("bold", round(H * 0.044))
        self.sub_font = load_font("regular", round(H * 0.026))
        self.intent_font = load_font("regular", round(H * 0.03))
        self.pad = round(H * 0.022)
        self.radius = round(H * 0.014)

    def alpha(self, cap, t):
        if t < cap["start"] + FADE_S:
            return max(0.0, (t - cap["start"]) / FADE_S)
        if t > cap["end"] - FADE_S:
            return max(0.0, (cap["end"] - t) / FADE_S)
        return 1.0

    def paint(self, img, cap, t):
        a = self.alpha(cap, t)
        if a <= 0:
            return
        if cap["kind"] == "intent":
            return self.paint_intent(img, cap, a)
        W, H, pad = self.W, self.H, self.pad
        draw = ImageDraw.Draw(img, "RGBA")
        max_text_w = W * 0.8 - 2 * pad
        title_lines = wrap(draw, cap["title"], self.title_font, max_text_w)
        sub_lines = wrap(draw, cap["subtitle"], self.sub_font, max_text_w) if cap.get("subtitle") else []
        th = self.title_font.size * 1.25
        sh = self.sub_font.size * 1.35
        text_w = max(
            [draw.textlength(line, font=self.title_font) for line in title_lines]
            + [draw.textlength(line, font=self.sub_font) for line in sub_lines]
        )
        box_w = min(W * 0.8, text_w + 2 * pad)
        box_h = pad * 1.6 + th * len(title_lines) + (sh * len(sub_lines) + pad * 0.3 if sub_lines else 0) + pad * 0.6
        x0 = (W - box_w) / 2
        y0 = H * 0.09 if cap.get("pos") == "top" else H * (1 - SAFE) - box_h
        draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=self.radius, fill=(*CARD_BG, int(225 * a)))
        draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + 4], radius=2, fill=(*ACCENT, int(255 * a)))
        y = y0 + pad * 0.9
        for line in title_lines:
            draw.text((W / 2, y + th / 2), line, font=self.title_font, fill=(255, 255, 255, int(255 * a)), anchor="mm")
            y += th
        if sub_lines:
            y += pad * 0.3
        for line in sub_lines:
            draw.text((W / 2, y + sh / 2), line, font=self.sub_font, fill=(203, 213, 225, int(255 * a)), anchor="mm")
            y += sh

    def paint_intent(self, img, cap, a):
        """One-line pill with an accent dot: what the viewer should watch for during the action."""
        W, H, font = self.W, self.H, self.intent_font
        draw = ImageDraw.Draw(img, "RGBA")
        pad_x, pad_y, dot = round(H * 0.02), round(H * 0.012), round(H * 0.012)
        text_w = draw.textlength(cap["title"], font=font)
        box_w, box_h = text_w + 2 * pad_x + dot * 2, font.size * 1.25 + 2 * pad_y
        x0 = (W - box_w) / 2
        y0 = H * 0.09 if cap["pos"] == "top" else H * (1 - SAFE) - box_h
        draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=box_h / 2, fill=(*CARD_BG, int(205 * a)))
        cy = y0 + box_h / 2
        dx = x0 + pad_x + dot / 2
        draw.ellipse([dx - dot / 2, cy - dot / 2, dx + dot / 2, cy + dot / 2], fill=(*ACCENT, int(255 * a)))
        draw.text((dx + dot * 1.5, cy), cap["title"], font=font, fill=(241, 245, 249, int(255 * a)), anchor="lm")


def build_captions(steps, spots, tmap, css_h):
    """Intent pills over each step's action window, then its outcome card over the hold. Boxes are CSS px."""

    def in_bottom_band(box):
        return box and (box["y"] + box["height"]) > BOTTOM_BAND * css_h

    caps = []
    for s in steps:
        pos = s.get("captionPos")
        if s.get("intent"):
            start, end = tmap(s["actionStart"]), tmap(s["actionEnd"])
            active = [sp["box"] for sp in spots if s["actionStart"] <= sp["start"] <= s["actionEnd"]]
            if end - start >= MIN_WINDOW_S:
                caps.append(
                    {
                        "kind": "intent",
                        "title": s["intent"],
                        "start": start,
                        "end": end,
                        "pos": pos or ("top" if any(map(in_bottom_band, active)) else "bottom"),
                    }
                )
        if not s.get("title"):
            continue
        start, end = tmap(s["actionEnd"]), tmap(s["holdEnd"])
        if end - start < MIN_WINDOW_S:
            continue
        if pos is None and not s.get("navigated") and in_bottom_band(s.get("target")):
            pos = "top"
        res = s.get("result")  # the step's checkpoint region: never cover the outcome the caption describes
        if pos is None and covers(res, "bottom", css_h) and not covers(res, "top", css_h):
            pos = "top"
        caps.append(
            {
                "kind": "caption",
                "title": s["title"],
                "subtitle": s.get("subtitle") or "",
                "start": start,
                "end": end,
                "pos": pos or "bottom",
            }
        )
    return caps


def covers(box, band, css_h):
    y0, y1 = CAPTION_BAND[band]
    return bool(box) and box["y"] < y1 * css_h and box["y"] + box["height"] > y0 * css_h


# --------------------------------------------------------------------------- spotlight


class Spotlight:
    """Dims everything but the active target. One feathered mask per spot, built once; fades scale the mask."""

    def __init__(self, spots, navigations, tmap, k, W, H, dim):
        self.black = Image.new("RGB", (W, H))
        self.items = []
        for sp in spots if dim > 0 else []:
            b, start, end = sp["box"], sp["start"], sp["end"]
            end = min([end] + [n for n in navigations if n > start + 0.05])  # the hole would sit on the next page
            if end - start < 0.4 or b["width"] * b["height"] > 0.4 * (W / k) * (H / k):
                continue
            pad, blur = 8 * k, 5 * k
            mask = Image.new("L", (W, H), round(255 * dim))
            ImageDraw.Draw(mask).rounded_rectangle(
                [
                    b["x"] * k - pad,
                    b["y"] * k - pad,
                    (b["x"] + b["width"]) * k + pad,
                    (b["y"] + b["height"]) * k + pad,
                ],
                radius=pad,
                fill=0,
            )
            self.items.append(
                {"start": tmap(start), "end": tmap(end), "mask": mask.filter(ImageFilter.GaussianBlur(blur))}
            )

    def active(self, t):
        return next((s for s in self.items if s["start"] <= t <= s["end"]), None)

    def paint(self, img, spot, t):
        a = min(1.0, (t - spot["start"]) / FADE_S, (spot["end"] - t) / FADE_S)
        mask = spot["mask"] if a >= 1 else spot["mask"].point(lambda v: int(v * max(0.0, a)))
        return Image.composite(self.black, img, mask)


# --------------------------------------------------------------------------- voiceover


def schedule_voiceover(out_dir, tmap, intro):
    """Places each line at its anchor or, if the previous line is still talking, right after it. Lines sharing a
    `holdAt` (one step, or a card) are one window: when its last line ends after that frame, the frame is held
    until it has finished and settled — except the intro card, which fit_intro sizes to its line. Freezes and cuts
    are added in time order, so earlier placements never move."""
    vo_path = out_dir / "voiceover.json"
    if not vo_path.exists():
        sys.exit('--voiceover needs output/voiceover.json: run `uv run "$SKILL/narrate.py"` first')
    lines = sorted(json.loads(vo_path.read_text())["lines"], key=lambda ln: (ln["holdAt"], ln["anchor"]))
    placed, clock = [], -LINE_GAP_S
    for i, line in enumerate(lines):
        start = max(tmap(line["anchor"]), clock + LINE_GAP_S)
        clock = start + line["seconds"]
        placed.append((out_dir / "vo" / line["file"], start))
        if i + 1 < len(lines) and lines[i + 1]["holdAt"] == line["holdAt"]:
            continue  # the window's last line decides its hold
        if line["kind"] == "intro" and intro:
            fit_intro(tmap, line["holdAt"], intro["end"], clock)
            continue
        short = clock + SETTLE_S - tmap(line["holdAt"])
        if short > 0:
            tmap.freezes.append((line["holdAt"], short))
    return placed


def fit_intro(tmap, hold_at, intro_end, speech_end):
    """Starts the first step INTRO_GAP_S after the intro line ends: the settled card (from `hold_at`) is held while
    the line runs past it, or trimmed when the line is shorter. The card's fade-out always plays, inside the gap."""
    delta = speech_end + INTRO_GAP_S - tmap(intro_end)
    if delta > 0:
        tmap.freezes.append((hold_at, delta))
        return
    cut = min(-delta, intro_end - CARD_FADE_S - hold_at)
    if cut > 0:
        tmap.cuts.append((hold_at, cut))


def mix_voiceover(out_dir, final, voice):
    """Lays every line at its output time, one pass, loudness-normalised, onto the finished video (copied, not
    re-encoded). apad + -shortest keep the audio exactly as long as the video."""
    inputs = [arg for path, _ in voice for arg in ("-i", str(path))]
    delays = "".join(f"[{i + 1}:a]adelay={round(start * 1000)}:all=1[a{i}];" for i, (_, start) in enumerate(voice))
    mixed = "".join(f"[a{i}]" for i in range(len(voice)))
    graph = (
        f"{delays}{mixed}amix=inputs={len(voice)}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad[aout]"
    )
    voiced = final.with_name(final.stem + "_voiced.mp4")
    run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(final), *inputs, "-filter_complex", graph]
        + ["-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest"]
        + ["-movflags", "+faststart", str(voiced)]
    )
    shutil.move(voiced, final)


# --------------------------------------------------------------------------- device frame


def build_frame_assets(out_dir, W, H, pad, bg):
    """Render bg.png (gradient + shadow) and mask.png (rounded corners) once."""
    cw, ch = W + 2 * pad, H + 2 * pad
    mask, shadow, bgp = out_dir / "_mask.png", out_dir / "_shadow.png", out_dir / "_bg.png"
    r = max(12, round(H * 0.02))
    geq = (
        f"if(gt(abs(W/2-X),W/2-{r})*gt(abs(H/2-Y),H/2-{r}),"
        f"if(lte(hypot({r}-(W/2-abs(W/2-X)),{r}-(H/2-abs(H/2-Y))),{r}),255,0),255)"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={W}x{H}",
            "-frames:v",
            "1",
            "-vf",
            f"format=gray,geq=lum='{geq}'",
            str(mask),
        ]
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(mask),
            "-frames:v",
            "1",
            "-vf",
            f"pad={cw}:{ch}:{pad}:{pad + round(pad * 0.25)}:black,gblur=sigma={round(pad * 0.28)},"
            "format=gray,geq=lum='p(X,Y)*0.55'",
            str(shadow),
        ]
    )
    c0, c1 = BG_COLORS[bg]
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"gradients=s={cw}x{ch}:c0={c0}:c1={c1}:x0=0:y0=0:x1={cw}:y1={ch}:type=linear:nb_colors=2:speed=0",
            "-i",
            str(shadow),
            "-frames:v",
            "1",
            "-filter_complex",
            f"[0:v]format=rgb24[g];[1:v]format=gray[a];color=c=black:s={cw}x{ch},format=rgb24[blk];"
            "[blk][a]alphamerge[sh];[g][sh]overlay=format=auto,format=rgb24",
            str(bgp),
        ]
    )
    return bgp, mask, cw, ch


def run(cmd):
    subprocess.run(cmd, check=True)


def has_encoder(name):
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return f" {name} " in encoders


# --------------------------------------------------------------------------- main


def main():
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
    args = ap.parse_args()

    out_dir = Path(args.task) / "output"
    raw = out_dir / "raw.mp4"
    if not raw.exists():
        sys.exit("No output/raw.mp4 found — run `node flow.mjs --record` first")
    final = out_dir / args.out
    src = probe(raw)
    SW, SH = src["width"], src["height"]
    steps, spots, navigations, video, intro = load_steps(out_dir)
    # Raw frames are dpr× the CSS viewport (2x capture); output is --scale× it, never upscaled past the source.
    css_w, css_h = int(video.get("width") or SW), int(video.get("height") or SH)
    dpr = SW / css_w
    if abs(dpr - round(dpr)) > 0.01 or abs(SH / css_h - dpr) > 0.01:
        sys.exit(f"{raw.name} {SW}x{SH} is not an integer multiple of the CSS viewport {css_w}x{css_h}")
    k = min(args.scale, dpr)
    W, H = round(css_w * k / 2) * 2, round(css_h * k / 2) * 2
    args.fps = args.fps or int(video.get("fps") or 25)

    tmap = TimeMap(steps, args.action_speed)
    voice = schedule_voiceover(out_dir, tmap, intro) if args.voiceover else []
    caps = [] if args.no_captions or args.voiceover else build_captions(steps, spots, tmap, css_h)
    painter = CaptionPainter(W, H)
    spotlight = Spotlight(spots, navigations, tmap, k, W, H, args.spot_dim)
    scale_flags = "bilinear" if args.fast else "lanczos"

    pad = 0 if args.no_frame else (round(W * 0.05) // 2) * 2
    filter_inputs, filter_complex = [], None
    if not args.no_frame:
        bgp, mask, cw, ch = build_frame_assets(out_dir, W, H, pad, args.bg)
        filter_inputs = ["-loop", "1", "-i", str(bgp), "-loop", "1", "-i", str(mask)]
        filter_complex = (
            "[0:v]format=rgba[v];[v][2:v]alphamerge=shortest=1[vr];"
            f"[1:v][vr]overlay=x={pad}:y={pad}:format=auto:shortest=1,format=yuv420p[out]"
        )

    if args.fast and has_encoder("h264_videotoolbox"):  # macOS hardware encoder
        vcodec = ["-c:v", "h264_videotoolbox", "-b:v", "6M"]
    elif args.fast:
        vcodec = ["-c:v", "libx264", "-crf", str(args.crf), "-preset", "ultrafast"]
    else:
        vcodec = ["-c:v", "libx264", "-crf", str(args.crf), "-preset", "slow", "-tune", "stillimage"]
    enc_cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{W}x{H}",
        "-r",
        str(args.fps),
        "-i",
        "-",
        *filter_inputs,
    ]
    if filter_complex:
        enc_cmd += ["-filter_complex", filter_complex, "-map", "[out]"]
    else:
        enc_cmd += ["-pix_fmt", "yuv420p"]
    enc_cmd += [*vcodec, "-g", "30", "-movflags", "+faststart", str(final)]

    # Downscale in ffmpeg (multithreaded) so Python receives output-size frames.
    decode_vf = [] if (W, H) == (SW, SH) else ["-vf", f"scale={W}:{H}:flags={scale_flags}"]
    decode = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(raw), *decode_vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
    )
    encode = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE)

    frame_size = W * H * 3
    src_fps, out_fps = src["fps"], args.fps
    next_slot, src_idx, written = 0.0, 0, 0
    while True:
        buf = decode.stdout.read(frame_size)
        if len(buf) < frame_size:
            break
        t_src = src_idx / src_fps
        t_out = tmap(t_src)
        src_idx += 1
        emitted = None
        while next_slot <= t_out + 1e-6:
            if emitted is None:
                active = [c for c in caps if c["start"] <= t_out <= c["end"]]
                spot = spotlight.active(t_out)
                if not active and spot is None:
                    emitted = buf  # passthrough — most frames
                else:
                    img = Image.frombytes("RGB", (W, H), buf)
                    if spot:
                        img = spotlight.paint(img, spot, t_out)
                    for cap in active:
                        painter.paint(img, cap, t_out)
                    emitted = img.tobytes()
            encode.stdin.write(emitted)
            written += 1
            next_slot += 1.0 / out_fps
    encode.stdin.close()
    decode.wait()
    encode.wait()
    if encode.returncode:
        sys.exit(f"encode failed ({encode.returncode})")
    if voice:
        mix_voiceover(out_dir, final, voice)

    size_mb = final.stat().st_size / 1e6
    if size_mb > args.max_mb and not args.fast:
        small = final.with_name(final.stem + "_small.mp4")
        run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(final),
                "-c:v",
                "libx264",
                "-crf",
                str(args.crf + 3),
                "-preset",
                "slow",
                "-tune",
                "stillimage",
                "-pix_fmt",
                "yuv420p",
                "-g",
                "30",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(small),
            ]
        )
        shutil.move(small, final)
        size_mb = final.stat().st_size / 1e6

    poster_t = min(1.0, max(0.0, written / out_fps - 0.1))
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{poster_t}",
            "-i",
            str(final),
            "-frames:v",
            "1",
            str(out_dir / "poster.png"),
        ]
    )
    for p in ("_mask.png", "_shadow.png", "_bg.png"):
        (out_dir / p).unlink(missing_ok=True)

    summary = {
        "final": str(final),
        "size_mb": round(size_mb, 2),
        "frames": written,
        "duration_s": round(written / out_fps, 2),
        "source_duration_s": round(src["duration"], 2),
        "source": f"{SW}x{SH} (dpr {dpr:g})",
        "canvas": f"{W + 2 * pad}x{H + 2 * pad}",
        "action_speed": args.action_speed,
        "sped_segments": len(tmap.segs),
        "captions": sum(c["kind"] == "caption" for c in caps),
        "intents": sum(c["kind"] == "intent" for c in caps),
        "spots": len(spotlight.items),
        "voice_lines": len(voice),
        "held_s": round(sum(sec for _, sec in tmap.freezes), 2),
        "cut_s": round(sum(sec for _, sec in tmap.cuts), 2),
        "over_budget": size_mb > args.max_mb,
    }
    (out_dir / "render.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
