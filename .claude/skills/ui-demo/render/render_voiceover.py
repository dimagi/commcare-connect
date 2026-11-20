"""render.py's voiceover: places narrate.py's lines in output time, holding frames so no line is cut, and mixes
them onto the video."""

import json
import shutil
import sys

from render_ffmpeg import run

LINE_GAP_S = 0.35  # silence between two voiceover lines
SETTLE_S = 0.5  # the frame stays still this long after a step's last line before the next action starts
INTRO_GAP_S = 0.6  # intro line end → first step start, the card's fade-out included
CARD_FADE_S = 0.4  # the tail of the intro capture that is the card fading out (Playwright: 300 ms) — never trimmed


def schedule_voiceover(out_dir, tmap, intro):
    """Places each line at its anchor or, if the previous line is still talking, right after it. Lines sharing a
    `holdAt` (one step, or a card) are one window: when its last line ends after that frame, the frame is held
    until it has finished and settled — except the intro card, which fit_intro sizes to its line. Freezes and cuts
    are added in time order, so earlier placements never move."""
    vo_path = out_dir / "voiceover.json"
    if not vo_path.exists():
        sys.exit('--voiceover needs output/voiceover.json: run `uv run "$SKILL/voiceover/narrate.py"` first')
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
    re-encoded). apad + -shortest keep the audio exactly as long as the video. No lines, nothing to do."""
    if not voice:
        return
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
