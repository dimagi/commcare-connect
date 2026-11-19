"""render.py's ffmpeg side: probing the capture, the decode and encode commands, the device frame around the
video, the size budget and the poster."""

import json
import shutil
import subprocess

BG_COLORS = {"dark": ("0x1e293b", "0x334155"), "light": ("0xe2e8f0", "0xcbd5e1")}


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


def run(cmd):
    subprocess.run(cmd, check=True)


def has_encoder(name):
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return f" {name} " in encoders


def decode_command(raw, canvas, fast):
    """ffmpeg decoding the capture to rgb24 frames on stdout, downscaled in ffmpeg (multithreaded) so Python
    receives output-size frames."""
    flags = "bilinear" if fast else "lanczos"
    same_size = (canvas.width, canvas.height) == (canvas.src_w, canvas.src_h)
    scale = [] if same_size else ["-vf", f"scale={canvas.width}:{canvas.height}:flags={flags}"]
    return ["ffmpeg", "-v", "error", "-i", str(raw), *scale, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]


def encode_command(args, final, canvas, frame):
    """ffmpeg reading rgb24 frames on stdin; `frame` is device_frame()'s inputs and graph, or None."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24"]
    cmd += ["-s", f"{canvas.width}x{canvas.height}", "-r", str(args.fps), "-i", "-"]
    if frame:
        inputs, graph = frame
        cmd += [*inputs, "-filter_complex", graph, "-map", "[out]"]
    else:
        cmd += ["-pix_fmt", "yuv420p"]
    return cmd + [*video_codec(args), "-g", "30", "-movflags", "+faststart", str(final)]


def video_codec(args):
    if args.fast and has_encoder("h264_videotoolbox"):  # macOS hardware encoder
        return ["-c:v", "h264_videotoolbox", "-b:v", "6M"]
    if args.fast:
        return ["-c:v", "libx264", "-crf", str(args.crf), "-preset", "ultrafast"]
    return ["-c:v", "libx264", "-crf", str(args.crf), "-preset", "slow", "-tune", "stillimage"]


def device_frame(out_dir, canvas, args):
    """(pad, frame): the device frame's margin, and its extra ffmpeg inputs (background, rounded-corner mask) with
    the graph that puts the video on them. (0, None) with --no-frame."""
    if args.no_frame:
        return 0, None
    pad = (round(canvas.width * 0.05) // 2) * 2
    bgp, mask, _, _ = build_frame_assets(out_dir, canvas, pad, args.bg)
    inputs = ["-loop", "1", "-i", str(bgp), "-loop", "1", "-i", str(mask)]
    graph = (
        "[0:v]format=rgba[v];[v][2:v]alphamerge=shortest=1[vr];"
        f"[1:v][vr]overlay=x={pad}:y={pad}:format=auto:shortest=1,format=yuv420p[out]"
    )
    return pad, (inputs, graph)


def build_frame_assets(out_dir, canvas, pad, bg):
    """Render bg.png (gradient + shadow) and mask.png (rounded corners) once."""
    W, H = canvas.width, canvas.height
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


def fit_size_budget(final, args):
    """Re-encodes once at crf+3 when the video is over --max-mb (not for --fast previews); returns its size in MB."""
    size_mb = final.stat().st_size / 1e6
    if size_mb <= args.max_mb or args.fast:
        return size_mb
    small = final.with_name(final.stem + "_small.mp4")
    run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(final), "-c:v", "libx264", "-crf", str(args.crf + 3)]
        + ["-preset", "slow", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-g", "30", "-c:a", "copy"]
        + ["-movflags", "+faststart", str(small)]
    )
    shutil.move(small, final)
    return final.stat().st_size / 1e6


def write_poster(final, poster, t):
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", str(final), "-frames:v", "1", str(poster)])
