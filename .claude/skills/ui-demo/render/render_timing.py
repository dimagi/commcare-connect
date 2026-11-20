"""render.py's timing: where each source moment lands in the output, and how many output frames it fills."""

import itertools


class TimeMap:
    """Piecewise-linear source→output time map. Action segments run at `speed`, everything else 1x, each freeze
    (source t, seconds) holds the frame at t for that long, and each cut (source t, seconds) drops that much 1x source
    from t on — the render loop skips frames whose output time does not advance."""

    def __init__(self, steps, speed, min_len=1.0):
        windows = [(s.get("actionStart"), s.get("actionEnd")) for s in steps] if speed > 1.0 else []
        self.segs = sorted((a, b) for a, b in windows if None not in (a, b) and b - a >= min_len)
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


def frame_repeats(tmap, src_fps, out_fps):
    """For each source frame in turn: its output time, and how many slots of the constant-fps output it fills — none
    when the time map squeezes it out, several while it is stretched or held."""
    next_slot = 0.0
    for src_idx in itertools.count():
        t_out = tmap(src_idx / src_fps)
        count = 0
        while next_slot <= t_out + 1e-6:
            count += 1
            next_slot += 1.0 / out_fps
        yield t_out, count
