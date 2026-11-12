#!/usr/bin/env python3
"""
autoclip.py - find the best moments in a long video and cut each one into its own clip.

Default (auto) mode scans the WHOLE video for high-energy moments (yelling, laughing,
explosions, panic, big reactions) and turns each one into a tight, phone-ready clip:

  * It looks for moments that SPIKE above what is happening around them (a sudden jump
    in loudness, a burst of new sound, a brighter/higher voice), not just loud stretches.
  * Each clip starts a couple of seconds before the action begins and ends right after
    the payoff, so there is no dead air, no long lead-in and no trailing chatter.
  * Clip length follows the moment (default 10-45 s) instead of a fixed 30 s.
  * Near-silent stretches (AFK, menus, dead air) are never clipped.
  * Clips never overlap and are named clip001.mp4, clip002.mp4, ... in video order.
  * Output is phone-shaped (9:16, 1080x1920), exported with platform-friendly settings
    (H.264/AAC 48 kHz, frame rate and bitrate capped, loudness around -14 LUFS).

Requirements: Python 3.8+, numpy (pip install numpy), FFmpeg + ffprobe on PATH.

Examples:
  python autoclip.py "C:/Videos/stream.mp4"                  # auto: every good moment
  python autoclip.py stream.mp4 --platform reels             # shorter clips, 30 fps, under 50 MB
  python autoclip.py stream.mp4 --quality 2                  # looser (more clips); 4 = pickier
  python autoclip.py stream.mp4 --skip 0-5:00 --skip 2:00:00-end
  python autoclip.py stream.mp4 --count 15                   # exactly the best 15 moments
  python autoclip.py stream.mp4 --length 30                  # force every clip to be 30 s
  python autoclip.py stream.mp4 --landscape                  # keep the original wide shape

--quality N   how much a moment must stand out to count (default 3). Lower = more clips.
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
NEG = -1e9          # "not allowed" marker for scores

# Per-platform export presets (min/max clip seconds, max fps, video Mbps ceiling, size cap in MB).
PLATFORMS = {
    "shorts": dict(min=10, max=45, fps=60, mbps=16, mb=None),   # YouTube Shorts: 60 fps, 12-20 Mbps
    "tiktok": dict(min=10, max=45, fps=30, mbps=15, mb=50),     # TikTok: 30 fps, 12-15 Mbps, under 50 MB
    "reels":  dict(min=8,  max=30, fps=30, mbps=12, mb=50),     # Reels: punchy, 30 fps, under 50 MB
}


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True).stdout
    return float(out.strip())


# --------------------------------------------------------------------------- analysis

def box(x, k):
    """Moving average that stays fair at the very start/end of the video."""
    kern = np.ones(k) / k
    return np.convolve(x, kern, mode="same") / np.convolve(np.ones(len(x)), kern, mode="same")


def robust_z(x, valid, cap=(-3.0, 6.0)):
    """Standardize with median/MAD so a few huge spikes don't squash everything else."""
    v = x[valid] if valid.any() else x
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med))) * 1.4826
    scale = max(mad, 0.5 * float(np.std(v)), 1e-6)
    return np.clip((x - med) / scale, *cap)


def audio_scores(path):
    """Return (score, rms): one value per WIN seconds each.

    score is an "arousal" score in robust standard deviations: how much each half-second
    stands out from the rest of the video through sudden loudness, new sound appearing
    (spectral flux) and a brighter, higher-pitched sound (shouting, laughing, screams).
    """
    print("Reading audio...")
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True, check=True)
    samples = np.frombuffer(proc.stdout, dtype=np.int16)
    n = int(SR * WIN)
    count = len(samples) // n
    if count < 8:
        sys.exit("No usable audio found in this video.")
    frames = samples[: count * n].reshape(count, n)

    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    hann = np.hanning(n).astype(np.float32)
    edges = np.searchsorted(freqs, np.geomspace(100, 7500, 21))
    rms = np.empty(count, dtype=np.float32)
    cent = np.empty(count, dtype=np.float32)
    bands = np.empty((count, 20), dtype=np.float32)
    chunk = 1000
    for a in range(0, count, chunk):
        x = frames[a:a + chunk].astype(np.float32) / 32768.0
        rms[a:a + chunk] = np.sqrt((x * x).mean(axis=1))
        mag = np.abs(np.fft.rfft(x * hann, axis=1))
        cent[a:a + chunk] = (mag * freqs).sum(axis=1) / (mag.sum(axis=1) + 1e-9)
        bands[a:a + chunk] = np.add.reduceat(mag, edges[:-1], axis=1)

    rms = rms.astype(np.float64)
    loud = box(rms, 5)
    ref = float(np.median(loud[loud > 0])) if (loud > 0).any() else 0.0
    valid = loud >= 0.25 * ref                      # not dead air

    L = np.log(rms + 1e-5)
    w = valid.astype(np.float64)
    def local_mean(x):                              # average of the surrounding 30 s, ignoring dead air
        return box(x * w, 61) / (box(w, 61) + 1e-9)
    over_local = L - local_mean(L)                  # louder than the last/next 30 s
    rise = np.maximum(0.0, L - np.concatenate([[L[0], L[0]], L[:-2]]))     # jump within ~1 s
    lb = np.log(bands.astype(np.float64) + 1e-3)
    flux = np.maximum(0.0, np.diff(lb, axis=0, prepend=lb[:1])).mean(axis=1)  # new sound appearing
    c = cent.astype(np.float64)
    bright = np.maximum(0.0, c / (local_mean(c) + 1e-6) - 1.0)              # higher/brighter than usual

    raw = (0.45 * robust_z(over_local, valid) + 0.25 * robust_z(flux, valid)
           + 0.20 * robust_z(rise, valid) + 0.10 * robust_z(bright, valid))
    raw = box(raw, 3)                               # ~1.5 s smoothing
    score = robust_z(raw, valid, cap=(-5.0, 12.0))
    return score, rms


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
    k = int(3 / WIN)                                # smooth over ~3 seconds
    scores = np.convolve(scores, np.ones(k) / k, mode="same")
    return scores / (scores.max() + 1e-9)


# --------------------------------------------------------------------------- time ranges

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


def trim_to_free(start, end, t, skips, min_keep=6.0):
    """Cut a clip back so it doesn't run into a skipped range. None if nothing usable is left."""
    for lo, hi in skips:
        if start < hi and end > lo:
            if lo <= t < hi:
                return None
            if hi <= t:
                start = max(start, hi)
            else:
                end = min(end, lo)
    return (start, end) if end - start >= min_keep else None


# --------------------------------------------------------------------------- picking clips

def closing(mask, k=5):
    """Fill gaps of up to k-1 windows inside a True/False array."""
    kern = np.ones(k)
    d = np.convolve(mask.astype(float), kern, mode="same") > 0
    return np.convolve(d.astype(float), kern, mode="same") >= k


def event_span(i, active, quiet, total, lead, tail, min_len, max_len):
    """Clip boundaries for the moment peaking at window i.

    Starts `lead` seconds before the action begins, ends `tail` seconds after it dies down,
    trims dead air off both ends, and keeps the length between min_len and max_len.
    """
    n = len(active)
    on = off = i
    while on > 0 and active[on - 1] and i - on < int(8 / WIN):
        on -= 1
    while off < n - 1 and active[off + 1] and off - i < int(12 / WIN):
        off += 1
    t = i * WIN
    start = on * WIN - lead
    end = (off + 1) * WIN + tail
    if end - start > max_len:                       # long moment: keep the peak and its build-up
        start = max(start, t - 0.4 * max_len)
        end = min(end, start + max_len)
    if end - start < min_len:                       # short moment: add context, mostly before it
        need = min_len - (end - start)
        start -= 0.65 * need
        end += 0.35 * need
    if start < 0:
        end, start = end - start, 0.0
    if end > total:
        start, end = max(0.0, start - (end - total)), total
    while start < t - 1 and quiet[min(n - 1, int(start / WIN))]:        # no dead air at the start
        start += WIN
    while end > t + 1 and quiet[min(n - 1, int((end - WIN) / WIN))]:    # ...or the end
        end -= WIN
    return start, end


def pick_clips(score, rms, total, skips, floor, quality, max_count, fixed_len=None,
               min_len=10.0, max_len=45.0, lead=2.5, tail=2.5, guard=3.0):
    """Pick the best moments, strongest first. Returns [(start, end, score)] in video order."""
    n = len(score)
    loud = box(rms, 5)
    ref = float(np.median(loud[loud > 0])) if (loud > 0).any() else 0.0
    quiet = loud < 0.25 * ref
    near_quiet = np.convolve(quiet.astype(float), np.ones(41), mode="same") > 0   # ~10 s either side
    cand = score.copy()
    cand[near_quiet] = NEG                          # coming back from dead air isn't a highlight
    for lo, hi in skips:
        cand[max(0, int(lo / WIN)): int(hi / WIN) + 1] = NEG
    active = closing(score > 0.5 * quality) & ~quiet     # where the action is "on"
    picks = []
    while len(picks) < max_count:
        i = int(cand.argmax())
        if cand[i] <= floor:
            break
        t = i * WIN
        if fixed_len:
            start = max(0.0, min(t - 0.35 * fixed_len, total - fixed_len))
            end = min(total, start + fixed_len)
        else:
            start, end = event_span(i, active, quiet, total, lead, tail, min_len, max_len)
        span = trim_to_free(start, end, t, skips)
        if span is None or any(span[0] < e2 - 0.5 and span[1] > s2 + 0.5 for s2, e2, _ in picks):
            cand[i] = NEG
            continue
        start, end = span
        picks.append((start, end, float(score[i])))
        cand[max(0, int((start - guard) / WIN)): int((end + guard) / WIN) + 1] = NEG
    return sorted(picks)


def pick_tiles(score, length, total, skips):
    """--all mode: pack as many non-overlapping fixed-length clips as will fit, best first."""
    s = np.maximum(score, 0.0)
    s = s.copy()
    for lo, hi in skips:
        s[max(0, int((lo - 0.35 * length) / WIN)): int((hi + 0.65 * length) / WIN) + 1] = 0
    block = max(0, int(length / WIN) - 1)
    picks = []
    while s.max() > 0:
        i = int(s.argmax())
        t = i * WIN
        start = max(0.0, min(t - 0.35 * length, total - length))
        end = min(total, start + length)
        ok = not any(start < hi and end > lo for lo, hi in skips)
        ok = ok and not any(start < e2 - 0.5 and end > s2 + 0.5 for s2, e2, _ in picks)
        if ok:
            picks.append((start, end, float(score[i])))
            s[max(0, i - block): i + block + 1] = 0
        else:
            s[i] = 0
    return sorted(picks)


# --------------------------------------------------------------------------- cutting

def cut(path, start, end, out, vertical, reencode, style, preset, normalize):
    dur = end - start
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}",
           "-i", str(path), "-t", f"{dur:.2f}"]
    if not vertical and not reencode:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]    # fast, no quality loss
        cmd.append(str(out))
        run(cmd)
        return
    vb = preset["mbps"] * 1e6                       # keep bitrate inside the platform's range...
    if preset["mb"]:                                # ...and the file under its size limit
        vb = min(vb, preset["mb"] * 0.92 * 8e6 / dur - 320e3)
    vb = int(max(vb, 2e6))
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-maxrate", str(vb), "-bufsize", str(2 * vb), "-pix_fmt", "yuv420p",
           "-fpsmax", str(preset["fps"]),
           "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart"]
    if normalize:                                   # ~-14 LUFS so platforms don't turn it down
        enc = ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"] + enc
    if vertical and style == "crop":
        # zoom into the center 9:16 slice (cuts off the sides)
        cmd += ["-vf", "crop=ih*9/16:ih,scale=1080:1920,setsar=1"] + enc
    elif vertical:
        # full picture over a blurred copy, sitting a little above center where phone apps
        # cover the least (they put captions/buttons at the bottom and the sides)
        fc = ("[0:v]split=2[bg][fg];"
              "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,boxblur=25:5[bgb];"
              "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgs];"
              "[bgb][fgs]overlay=x=(W-w)/2:y='clip(860-h/2,0,H-h)',setsar=1[v]")
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a?"] + enc
    else:
        cmd += enc
    cmd.append(str(out))
    run(cmd)


def fmt(t):
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def main():
    ap = argparse.ArgumentParser(description="Auto-cut the best moments of a video into phone-ready clips.")
    ap.add_argument("video")
    ap.add_argument("--platform", choices=list(PLATFORMS), default="shorts",
                    help="export preset: shorts (default), tiktok or reels. Sets clip length range, frame rate and bitrate/size limits")
    ap.add_argument("--quality", type=float, default=3.0,
                    help="auto mode: how much a moment must stand out (default 3; lower = more clips, higher = fewer)")
    ap.add_argument("--per", type=float, default=None, metavar="MIN",
                    help="auto mode: cap at one clip per MIN minutes of video on average")
    ap.add_argument("--count", type=int, default=None, help="make exactly the best N clips instead of auto mode")
    ap.add_argument("--all", action="store_true", help="tile the whole video: as many non-overlapping clips as fit, good or not")
    ap.add_argument("--length", type=float, default=None,
                    help="force every clip to this many seconds (default: each clip fits its moment)")
    ap.add_argument("--min-length", type=float, default=None, help="shortest clip in seconds (default from --platform)")
    ap.add_argument("--max-length", type=float, default=None, help="longest clip in seconds (default from --platform)")
    ap.add_argument("--min-gap", type=float, default=3.0, help="seconds of breathing room kept around each clip (default 3)")
    ap.add_argument("--out", default="clips", help="output folder (default ./clips)")
    ap.add_argument("--use-video", action="store_true", help="also score visual action (slower)")
    ap.add_argument("--landscape", action="store_true",
                    help="keep the video's original shape instead of making phone-shaped 9:16 clips (the default)")
    ap.add_argument("--vertical", action="store_true", help=argparse.SUPPRESS)  # old option, 9:16 is the default now
    ap.add_argument("--style", choices=["blur", "crop"], default="blur",
                    help="phone-shaped clips: 'blur' keeps the whole picture over a blurred background (default); 'crop' zooms into the center")
    ap.add_argument("--no-normalize", action="store_true", help="don't level the audio to about -14 LUFS")
    ap.add_argument("--skip", action="append", default=[], metavar="START-END",
                    help="ignore a time range (repeatable). Examples: --skip 0-5:00  --skip 1:20:00-1:35:00  --skip 2:00:00-end")
    ap.add_argument("--reencode", action="store_true", help="with --landscape: re-encode for frame-accurate cuts (default there is fast stream copy)")
    a = ap.parse_args()

    video = Path(a.video)
    if not video.exists():
        sys.exit(f"File not found: {video}")
    preset = PLATFORMS[a.platform]
    min_len = a.min_length if a.min_length is not None else preset["min"]
    max_len = a.max_length if a.max_length is not None else preset["max"]
    if min_len > max_len:
        sys.exit("--min-length can't be longer than --max-length.")
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    total = duration(video)
    skips = parse_skips(a.skip, total)
    for lo, hi in skips:
        print(f"Skipping {fmt(lo)} - {fmt(hi)}")
    score, rms = audio_scores(video)
    if a.use_video:
        score = score + 3.0 * scene_scores(video, len(score))

    usable_min = max(0.0, total - skipped_seconds(skips)) / 60
    if a.all:
        length = a.length or 30.0
        print(f"Mode: whole video ({length:g}-second clips, as many as fit)")
        picks = pick_tiles(score, length, total, skips)
    else:
        if a.count is not None:
            floor, max_count = -1e8, a.count
            print(f"Mode: best {a.count} moments")
        else:
            floor = a.quality
            max_count = 10**9 if a.per is None else max(1, int(round(usable_min / a.per)))
            print(f"Mode: auto (quality {a.quality:g}) over {usable_min:.1f} min of video")
        picks = pick_clips(score, rms, total, skips, floor, a.quality, max_count, fixed_len=a.length,
                           min_len=min_len, max_len=max_len, guard=a.min_gap)
    if not picks:
        sys.exit("Couldn't find any highlights. Try a lower --quality (like 2).")

    print(f"\nCutting {len(picks)} clips into {out_dir}/")
    rows = []
    for n, (s, e, sc) in enumerate(picks, 1):
        name = out_dir / f"clip{n:03d}.mp4"           # files are numbered in video order
        cut(video, s, e, name, not a.landscape, a.reencode, a.style, preset, not a.no_normalize)
        print(f"  cut {name.name} ({n}/{len(picks)})")
        rows.append((sc, f"{name.name}  [{fmt(s)} - {fmt(e)}]  {e - s:.0f}s  score {sc:.1f}"))
    rows.sort(key=lambda r: -r[0])                    # the list is ranked best first
    log = [f"{rank}. {line}" for rank, (_, line) in enumerate(rows, 1)]
    print("\nBest clips first:")
    for line in log:
        print("  " + line)
    (out_dir / "clips_list.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    if usable_min > 0:
        print(f"\nDone: {len(picks)} clips from {usable_min:.1f} min of video "
              f"(about 1 clip per {usable_min / len(picks):.1f} min). Timestamps saved to clips_list.txt")


if __name__ == "__main__":
    main()
