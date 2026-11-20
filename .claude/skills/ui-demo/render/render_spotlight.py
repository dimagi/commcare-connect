"""render.py's spotlight: dims everything but the target a step clicks or types into."""

from PIL import Image, ImageDraw, ImageFilter
from render_captions import FADE_S


def until_next_page(spots, navigations):
    """Ends each spot at the next navigation after it: the hole would sit on the next page."""
    return [{**sp, "end": min([sp["end"]] + [n for n in navigations if n > sp["start"] + 0.05])} for sp in spots]


class Spotlight:
    """Dims everything but the active target. One feathered mask per spot, built once; fades scale the mask."""

    def __init__(self, spots, tmap, canvas, dim):
        W, H, k = canvas.width, canvas.height, canvas.k
        self.black = Image.new("RGB", (W, H))
        self.items = []
        for sp in spots if dim > 0 else []:
            b, start, end = sp["box"], sp["start"], sp["end"]
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
