#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11,<3.12"
# dependencies = ["chatterbox-tts==0.1.2", "soundfile", "setuptools<81"]
# ///
# setuptools<81: perth, Chatterbox's watermarker, imports pkg_resources
"""narrate.py — voiceover lines for ui-demo. Never touches the browser or the video.

Reads  output/steps.json
Writes output/vo/<hash>.wav (one per line) + output/voiceover.json, which `render.py --voiceover` mixes in

The voiceover is a presenter giving the demo live — first person, talking while they click ("Let me start a draft,
and I'll put a typo in the budget on purpose"), then reacting to what the screen shows ("There, it's caught on save").
It is written per step as `say` in flow.mjs (the runner copies it to steps.json, where it can be edited and re-voiced):
  INTRO.say / OUTRO.say          spoken over the intro / outro card, from its start
  step.say.intent                spoken from step.actionStart, while the action plays
  step.say.outcome               spoken from step.actionEnd, over the settled page; null mutes either line
Without a `say`, the caption text is read out instead — it works, but sounds like captions read aloud, so a warning
is printed. render.py stretches holds so no line is ever cut.

Defaults: Chatterbox's built-in voice, exaggeration 0.5, cfg 0.5, tempo 0.9 (a little slower than speech, so a
viewer can follow the clicks). A clip is cached by its text + voice settings, so editing one line in steps.json
re-voices only that line. The first run installs torch into uv's own cached environment and downloads the
Chatterbox weights (~3.2 GB). Runs on an NVIDIA GPU, then Apple's GPU, else the CPU (much slower).

Usage, from the task folder:
  uv run "$SKILL/narrate.py" [--task DIR] [--voice ref.wav] [--exaggeration 0.5] [--cfg-weight 0.5] [--tempo 0.9]
                             [--fresh]
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

SILENCE_THRESHOLD = 0.01
EDGE_PADDING = 0.06  # seconds of room left before the first and after the last sound
CARD_SETTLED_S = 0.1  # how far inside the intro/outro capture the card frame to hold sits


def main():
    args = parse_args()
    out_dir = Path(args.task) / "output"
    data = json.loads((out_dir / "steps.json").read_text())
    lines = script_lines(data)
    if not lines:
        sys.exit("steps.json has nothing to say: no intro/outro text, intents or captions")

    vo_dir = out_dir / "vo"
    vo_dir.mkdir(exist_ok=True)
    settings = [args.voice, args.exaggeration, args.cfg_weight, args.tempo]
    voice_lines(lines, vo_dir, settings, args)
    remove_stale_clips(vo_dir, lines)
    (out_dir / "voiceover.json").write_text(json.dumps({"settings": settings, "lines": lines}, indent=2))
    print(f"[vo] {len(lines)} lines, {sum(line['seconds'] for line in lines):.1f}s of speech → output/voiceover.json")


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default=".", help="task folder holding output/")
    ap.add_argument("--voice", default=None, help="reference wav to clone (5-9 s of clean speech); default: built-in")
    ap.add_argument("--exaggeration", type=float, default=0.5, help="energy")
    ap.add_argument("--cfg-weight", type=float, default=0.5, help="pacing; lower = slower, more deliberate")
    ap.add_argument("--tempo", type=float, default=0.9, help="ffmpeg atempo applied after generation, pitch kept")
    ap.add_argument("--fresh", action="store_true", help="new takes for every line (Chatterbox is stochastic)")
    return ap.parse_args()


def voice_lines(lines, vo_dir, settings, args):
    """Gives each line its clip (`file`, `seconds`): cached by text + settings, so only new or edited lines are
    voiced, unless --fresh."""
    narrator = Narrator(args)
    for line in lines:
        wav = vo_dir / f"{hashlib.sha256(json.dumps([line['text'], *settings]).encode()).hexdigest()[:16]}.wav"
        if args.fresh or not wav.exists():
            narrator.speak(line["text"], wav)
        line["file"] = wav.name
        line["seconds"] = duration(wav)
        print(f"[vo] {line['seconds']:5.2f}s  {line['kind']:<7} {line['text']}", flush=True)


def remove_stale_clips(vo_dir, lines):
    used = {line["file"] for line in lines}
    for stale in vo_dir.glob("*.wav"):
        if stale.name not in used:
            stale.unlink()


def script_lines(data):
    """Every spoken line with the source-time it belongs to (`anchor`) and the settled frame render.py may hold to fit
    it (`holdAt`): the intro card once faded in, a step's last hold frame, the outro card's last frame."""
    return card_line(data, "intro") + step_lines(data["steps"]) + card_line(data, "outro")


def card_line(data, key):
    card = data.get(key) or {}
    text = card.get("say") or warn_fallback(key, sentence(card.get("title"), card.get("description")))
    hold_at = card["start"] + CARD_SETTLED_S if key == "intro" else card["end"] - CARD_SETTLED_S
    return [{"kind": key, "text": text, "anchor": card["start"], "holdAt": hold_at}] if text else []


def step_lines(steps):
    lines = []
    for s in steps:
        say = s.get("say") or warn_fallback(s["name"], {})
        intent = say.get("intent", s.get("intent"))
        outcome = say.get("outcome", sentence(s.get("title"), s.get("subtitle")))
        if intent:
            lines.append(
                {"kind": "intent", "text": sentence(intent), "anchor": s["actionStart"], "holdAt": s["holdEnd"]}
            )
        if outcome:
            lines.append(
                {"kind": "outcome", "text": sentence(outcome), "anchor": s["actionEnd"], "holdAt": s["holdEnd"]}
            )
    return lines


def warn_fallback(where, fallback):
    print(f"[vo] warning: {where} has no `say`; reading its caption text instead", file=sys.stderr)
    return fallback


def sentence(*parts):
    """Joins caption fragments into spoken sentences; a missing full stop makes Chatterbox trail off."""
    parts = [p.strip() for p in parts if p and p.strip()]
    return " ".join(p if p[-1] in ".!?" else p + "." for p in parts)


def duration(wav):
    import soundfile

    info = soundfile.info(str(wav))
    return round(info.frames / info.samplerate, 3)


class Narrator:
    """The model loads on the first uncached line, once."""

    def __init__(self, args):
        self.args = args
        self._model = None

    @property
    def model(self):
        if self._model is None:
            import torch
            from chatterbox.tts import ChatterboxTTS

            device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
            print(f"[vo] loading Chatterbox on {device}…", flush=True)
            self._model = ChatterboxTTS.from_pretrained(device=device)
        return self._model

    def speak(self, text, wav_path):
        import torchaudio

        a = self.args
        wav = self.model.generate(
            text, audio_prompt_path=a.voice, exaggeration=a.exaggeration, cfg_weight=a.cfg_weight
        )
        # write to temp files and move into wav_path last: a half-finished clip there would be reused as a cache hit
        raw = wav_path.with_suffix(".raw.wav")
        torchaudio.save(str(raw), trim_silence(wav, self.model.sr), self.model.sr)
        if abs(a.tempo - 1) > 1e-3:
            tmp = wav_path.with_suffix(".tempo.wav")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-filter:a", f"atempo={a.tempo}", str(tmp)],
                check=True,
            )
            raw.unlink()
            raw = tmp
        raw.replace(wav_path)


def trim_silence(wav, sample_rate):
    """Cuts leading/trailing silence so the hold render.py adds matches the actual speech."""
    loud = (wav.abs().max(dim=0).values > SILENCE_THRESHOLD).nonzero()
    if len(loud) == 0:
        return wav
    padding = int(EDGE_PADDING * sample_rate)
    return wav[:, max(0, int(loud[0]) - padding) : min(wav.shape[1], int(loud[-1]) + padding)]


if __name__ == "__main__":
    main()
