"""render.py's captions: the intent pill during a step's action and the outcome card after it, drawn with PIL."""

from pathlib import Path

from PIL import ImageDraw, ImageFont

FADE_S = 0.2
MIN_WINDOW_S = 0.8  # an intent/caption shorter than this (after speed-up) is dropped, not flashed
SAFE = 0.05  # safe margin as a fraction of the frame
BOTTOM_BAND = 0.72  # a clicked target lower than this flips the caption to the top
CAPTION_BAND = {"bottom": (0.78, 0.95), "top": (0.09, 0.26)}  # vertical extent of a two-line card, fraction of H
CARD_BG = (17, 24, 39)  # slate-900
ACCENT = (237, 106, 20)  # Connect orange
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
    caps = []
    for s in steps:
        caps += intent_pill(s, spots, tmap, css_h) + outcome_card(s, tmap, css_h)
    return caps


def intent_pill(s, spots, tmap, css_h):
    if not s.get("intent"):
        return []
    start, end = tmap(s["actionStart"]), tmap(s["actionEnd"])
    if end - start < MIN_WINDOW_S:
        return []
    active = [sp["box"] for sp in spots if s["actionStart"] <= sp["start"] <= s["actionEnd"]]
    pos = s.get("captionPos") or ("top" if any(in_bottom_band(box, css_h) for box in active) else "bottom")
    return [{"kind": "intent", "title": s["intent"], "start": start, "end": end, "pos": pos}]


def outcome_card(s, tmap, css_h):
    if not s.get("title"):
        return []
    start, end = tmap(s["actionEnd"]), tmap(s["holdEnd"])
    if end - start < MIN_WINDOW_S:
        return []
    return [
        {
            "kind": "caption",
            "title": s["title"],
            "subtitle": s.get("subtitle") or "",
            "start": start,
            "end": end,
            "pos": card_position(s, css_h),
        }
    ]


def card_position(s, css_h):
    """`captionPos` when set; else top when the clicked target sits in the bottom band (and the step stayed on its
    page), or when the step's checkpoint region would sit under a bottom caption: never cover the outcome."""
    pos = s.get("captionPos")
    if pos is not None:
        return pos or "bottom"
    target_low = not s.get("navigated") and in_bottom_band(s.get("target"), css_h)
    res = s.get("result")
    result_low = covers(res, "bottom", css_h) and not covers(res, "top", css_h)
    return "top" if target_low or result_low else "bottom"


def in_bottom_band(box, css_h):
    return box and (box["y"] + box["height"]) > BOTTOM_BAND * css_h


def covers(box, band, css_h):
    y0, y1 = CAPTION_BAND[band]
    return bool(box) and box["y"] < y1 * css_h and box["y"] + box["height"] > y0 * css_h
