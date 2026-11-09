#!/usr/bin/env python3
"""
autoclip.py - find the best moments in a long video and cut each one into its own clip.

Default (auto) mode scans the WHOLE video and keeps every moment that stands out from
what is going on around it (reactions, yelling, explosions, big gameplay moments).
Longer or more eventful videos get more clips, quiet or dead stretches get none.
Clips never overlap and are named clip001.mp4, clip002.mp4, ... in video order.
Clips are always phone-shaped (9:16, 1080x1920) for Shorts/TikTok/Reels unless you pass --landscape.

Requirements: Python 3.8+, numpy (pip install numpy), FFmpeg + ffprobe on PATH.

Examples:
  python autoclip.py "C:/Videos/stream.mp4"                    # auto: as many good clips as it finds
  python autoclip.py stream.mp4 --landscape                    # keep the original wide shape instead of phone-size
  python autoclip.py stream.mp4 --quality 1                    # pickier or looser (see below)
  python autoclip.py stream.mp4 --skip 0-5:00 --skip 2:00:00-end
  python autoclip.py stream.mp4 --count 15                     # exactly the best 15 instead
  python autoclip.py stream.mp4 --all                          # tile the whole video, every part gets a clip

--quality N   how much a moment must stand out to count (default 2).
              Lower = more clips (1 is loose), higher = fewer, stronger clips (3 is picky).
--per MIN     optional cap: at most one clip per MIN minutes of video on average.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 16000          # analysis sample rate
WIN = 0.5           # seconds per scoring window


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def audio_scores(path):
    """Return (score, loudness): one value per WIN seconds each."""
    print("Reading audio...")
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True, check=True)
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(SR * WIN)
    count = len(samples) // n
    if count == 0:
        sys.exit("No audio found in this video.")
    frames = samples[: count * n].reshape(count, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))

    # loudness relative to the local average (so a quiet video still finds its peaks)
    k = int(30 / WIN)
    kernel = np.ones(k) / k
    # divide by how much of the window fits inside the video, so the very start/end aren't inflated
    local = np.convolve(rms, kernel, mode="same") / np.convolve(np.ones(len(rms)), kernel, mode="same") + 1e-6
    rel = rms / local

    # sudden jumps (reactions, impacts)
    jump = np.clip(np.diff(rms, prepend=rms[0]), 0, None)
    jump = jump / (jump.max() + 1e-9)

    rel = rel / (rel.max() + 1e-9)
    return 0.7 * rel + 0.3 * jump, rms


def scene_scores(path, n_windows):
    """Per-window count of scene changes (visual action). Slower; optional."""
    print("Scanning video for visual action (this can take a while)...")
    proc = subprocess.run(
        ["ffmpeg", "-i", str(path), "-vf",
         "select='gt(scene,0.25)',showinfo", "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    scores = np.zeros(n_windows, dtype=np.float32)
    for m in re.finditer(r"pts_time:([0-9.]+)", proc.stderr):
        i = int(float(m.group(1)) / WIN)
        if 0 <= i < n_windows:
            scores[i] += 1
    # smooth over ~3 seconds
    k = int(3 / WIN)
    scores = np.convolve(scores, np.ones(k) / k, mode="same")
    return scores / (scores.max() + 1e-9)


def parse_time(text, total):
    """'90', '1:30' or '1:02:03' -> seconds. 'start' = 0, 'end' = end of video."""
    t = text.strip().lower()
    if t == "start":
        return 0.0
    if t == "end":
        return total
    secs = 0.0
    for part in t.split(":"):
        secs = secs * 60 + float(part)
    return secs


def parse_skips(items, total):
    skips = []
    for item in items:
        try:
            a, b = item.split("-", 1)
            lo, hi = parse_time(a, total), parse_time(b, total)
        except ValueError:
            sys.exit(f'Bad --skip value "{item}". Use START-END, like 0-5:00 or 1:20:00-1:35:00')
        if lo >= total:
            print(f"Warning: --skip {item} ignored, the video is only {fmt(total)} long")
            continue
        if hi <= lo:
            sys.exit(f'Bad --skip value "{item}": the end must be after the start.')
        skips.append((lo, min(hi, total)))
    return skips


def skipped_seconds(skips):
    merged = []
    for lo, hi in sorted(skips):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return sum(hi - lo for lo, hi in merged)


def apply_skips(s, length, skips):
    """Zero out peaks whose clip (from t-0.6*length to t+0.4*length) would touch a skipped range."""
    for lo, hi in skips:
        i0 = max(0, int((lo - 0.4 * length) / WIN))
        i1 = int((hi + 0.6 * length) / WIN) + 1
        s[i0:i1] = 0


def clip_span(i, length, total):
    t = i * WIN
    start = max(0.0, t - length * 0.6)       # lead-in before the peak
    end = min(total, start + length)
    start = max(0.0, end - length)
    return start, end


def pick_peaks(scores, count, length, min_gap, total, skips=(), no_overlap=False):
    """Fixed-count mode: take the highest window, block out neighbours, repeat."""
    s = np.convolve(scores, np.ones(5) / 5, mode="same")  # ~2.5 s smoothing
    block = int(min_gap / WIN)
    apply_skips(s, length, skips)
    picks = []
    while len(picks) < count and s.max() > 0:
        i = int(s.argmax())
        start, end = clip_span(i, length, total)
        if any(start < hi and end > lo for lo, hi in skips):
            s[max(0, i - block): i + block + 1] = 0
            continue
        if no_overlap and any(start < e2 - 0.5 and end > s2 + 0.5 for s2, e2, _ in picks):
            s[i] = 0   # would overlap a clip we already have
            continue
        picks.append((start, end, float(s[i])))
        s[max(0, i - block): i + block + 1] = 0
    return sorted(picks)


def pick_auto(scores, loudness, length, total, skips, quality, max_count):
    """Auto mode: keep every moment that stands out from the rest of the video.

    A moment counts if its score is `quality` robust-standard-deviations above the
    video's typical score. Strongest moments claim their spot first, clips never overlap,
    and near-silent stretches (AFK, dead air) are ignored.
    """
    s = np.convolve(scores, np.ones(5) / 5, mode="same")
    # ignore near-silence: nothing worth clipping happens there
    loud = np.convolve(loudness, np.ones(5) / 5, mode="same")
    ref = float(np.median(loud[loud > 0])) if (loud > 0).any() else 0.0
    quiet = (loud < 0.25 * ref).astype(np.float32)
    # also ignore ~10 s around dead air, so coming back from silence doesn't look like a highlight
    quiet = np.convolve(quiet, np.ones(21), mode="same") > 0
    s[quiet] = 0
    apply_skips(s, length, skips)
    pos = s[s > 0]
    if pos.size == 0:
        return [], 0.0
    med = float(np.median(pos))
    mad = float(np.median(np.abs(pos - med))) * 1.4826
    floor = med + quality * max(mad, 1e-6)
    block = max(0, int(length / WIN) - 1)
    picks = []
    while len(picks) < max_count:
        i = int(s.argmax())
        if s[i] <= floor:
            break
        start, end = clip_span(i, length, total)
        ok = not any(start < hi and end > lo for lo, hi in skips)
        ok = ok and not any(start < e2 - 0.5 and end > s2 + 0.5 for s2, e2, _ in picks)
        if ok:
            picks.append((start, end, float(s[i])))
            s[max(0, i - block): i + block + 1] = 0
        else:
            s[i] = 0
    return sorted(picks), floor


def cut(path, start, end, out, vertical, reencode, style="blur"):
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}",
           "-i", str(path), "-t", f"{end - start:.2f}"]
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart"]
    if vertical and style == "crop":
        # zoom into the center 9:16 slice (cuts off the sides)
        cmd += ["-vf", "crop=ih*9/16:ih,scale=1080:1920,setsar=1"] + enc
    elif vertical:
        # full picture fitted in the middle of a 1080x1920 frame,
        # with a blurred copy of the video filling the top and bottom
        fc = ("[0:v]split=2[bg][fg];"
              "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,boxblur=25:5[bgb];"
              "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgs];"
              "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a?"] + enc
    elif reencode:
        cmd += enc
    else:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]  # fast, no quality loss
    cmd.append(str(out))
    run(cmd)


def fmt(t):
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def main():
    ap = argparse.ArgumentParser(description="Auto-cut highlight clips from a video.")
    ap.add_argument("video")
    ap.add_argument("--count", type=int, default=None, help="make exactly the best N clips instead of auto mode")
    ap.add_argument("--all", action="store_true", help="tile the whole video: as many non-overlapping clips as fit, good or not")
    ap.add_argument("--quality", type=float, default=2.0,
                    help="auto mode: how much a moment must stand out (default 2; lower = more clips, higher = fewer)")
    ap.add_argument("--per", type=float, default=None, metavar="MIN",
                    help="auto mode: cap at one clip per MIN minutes of video on average")
    ap.add_argument("--length", type=float, default=30, help="clip length in seconds (default 30)")
    ap.add_argument("--min-gap", type=float, default=None, help="with --count: min seconds between clip peaks (default 60)")
    ap.add_argument("--out", default="clips", help="output folder (default ./clips)")
    ap.add_argument("--use-video", action="store_true", help="also score visual action (slower)")
    ap.add_argument("--landscape", action="store_true",
                    help="keep the video's original shape instead of making phone-shaped 9:16 clips (the default)")
    ap.add_argument("--vertical", action="store_true", help=argparse.SUPPRESS)  # old option, 9:16 is the default now
    ap.add_argument("--style", choices=["blur", "crop"], default="blur",
                    help="phone-shaped clips: 'blur' keeps the whole picture over a blurred background (default); 'crop' zooms into the center")
    ap.add_argument("--skip", action="append", default=[], metavar="START-END",
                    help="ignore a time range (repeatable). Examples: --skip 0-5:00  --skip 1:20:00-1:35:00  --skip 2:00:00-end")
    ap.add_argument("--reencode", action="store_true", help="re-encode instead of fast stream copy (frame-accurate cuts)")
    a = ap.parse_args()

    video = Path(a.video)
    if not video.exists():
        sys.exit(f"File not found: {video}")
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    total = duration(video)
    skips = parse_skips(a.skip, total)
    for lo, hi in skips:
        print(f"Skipping {fmt(lo)} - {fmt(hi)}")
    scores, loudness = audio_scores(video)
    if a.use_video:
        v = scene_scores(video, len(scores))
        scores = 0.65 * scores + 0.35 * v

    usable_min = max(0.0, total - skipped_seconds(skips)) / 60
    if a.all:
        print("Mode: whole video (as many non-overlapping clips as fit)")
        picks = pick_peaks(scores, 10**9, a.length, 0, total, skips, True)
    elif a.count is not None:
        min_gap = a.min_gap if a.min_gap is not None else 60
        print(f"Mode: best {a.count} clips")
        picks = pick_peaks(scores, a.count, a.length, min_gap, total, skips)
    else:
        max_count = 10**9 if a.per is None else max(1, int(round(usable_min / a.per)))
        picks, _ = pick_auto(scores, loudness, a.length, total, skips, a.quality, max_count)
        print(f"Mode: auto (quality {a.quality}) over {usable_min:.1f} min of video")
    if not picks:
        sys.exit("Couldn't find any highlights. Try a lower --quality (like 1).")

    print(f"\nCutting {len(picks)} clips into {out_dir}/")
    log = []
    for n, (s, e, sc) in enumerate(picks, 1):
        name = out_dir / f"clip{n:03d}.mp4"
        cut(video, s, e, name, not a.landscape, a.reencode, a.style)
        line = f"{name.name}  [{fmt(s)} - {fmt(e)}]  score {sc:.2f}"
        print("  " + line)
        log.append(line)
    (out_dir / "clips_list.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    if usable_min > 0:
        print(f"\nDone: {len(picks)} clips from {usable_min:.1f} min of video "
              f"(about 1 clip per {usable_min / len(picks):.1f} min). Timestamps saved to clips_list.txt")


if __name__ == "__main__":
    main()
