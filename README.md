# autoclip

Free, local auto-clipper. Point it at a long video (a stream, a gameplay session, a podcast)
and it finds the best moments and cuts each one into its own clip. Nothing is uploaded
anywhere: it runs on your own computer with FFmpeg and a little Python.

- Scans the **whole video** and keeps every moment that stands out (yelling, reactions,
  explosions, big gameplay moments). More eventful video = more clips; dead air = none.
- Clips never overlap and are named `clip001.mp4`, `clip002.mp4`, ... in video order.
- Clips come out **phone-shaped (9:16, 1080x1920)** by default, ready for YouTube Shorts, TikTok and Reels. Widescreen, portrait and rotated phone footage all end up the same shape.
- Optional `--skip` ranges to ignore an intro, a break, or anything after a certain time.
- Writes `clips_list.txt` with each clip's start/end time in the original video.

## Install

You need [Python 3.8+](https://www.python.org/downloads/) and [FFmpeg](https://ffmpeg.org/download.html)
(`ffmpeg` and `ffprobe` must be on your PATH).

```
pip install numpy
```

Windows tip: `winget install ffmpeg`, then reopen your terminal.
macOS: `brew install ffmpeg`. Linux: `sudo apt install ffmpeg`.

Then download `autoclip.py` from this repo (or `git clone` it).

## Use

```
python autoclip.py "C:\Videos\stream.mp4"
```

Clips go into a `clips` folder next to where you ran the command. Choose another folder with `--out`:

```
python autoclip.py "C:\Videos\stream.mp4" --out "D:\MyClips"
```

Use a different `--out` folder for each video: every run starts at `clip001.mp4` and overwrites
files with the same name.

### Phone-shaped clips (the default)

Every clip is 1080x1920 (9:16), so it fills a phone screen for Shorts, TikTok and Reels. Your full
picture sits in the middle of the frame over a blurred copy of the video. Add `--style crop` to zoom
into the center instead (cuts off the sides).

Phone-shaped clips are re-encoded, so they take longer than plain cuts. To keep the original wide
shape (and get fast, lossless cuts), add `--landscape`:

```
python autoclip.py stream.mp4 --landscape
```

### Skip parts of the video

```
python autoclip.py stream.mp4 --skip 0-10:00 --skip 1:20:00-1:35:00 --skip 2:00:00-end
```

Times are seconds (`90`), `M:SS` (`5:00`) or `H:MM:SS` (`1:20:00`). Use `start` / `end` as range
ends. A skip that starts after the end of the video is ignored with a warning.

## Options

| Option | What it does |
|---|---|
| `--quality N` | How much a moment must stand out to count (default `2`). Lower = more clips (`1` is loose), higher = fewer, stronger clips (`3` is picky). |
| `--per MIN` | Optional cap: at most one clip per `MIN` minutes of video on average. |
| `--length SEC` | Clip length in seconds (default `30`). |
| `--landscape` | Keep the video's original shape instead of making phone-shaped 9:16 clips. |
| `--style blur\|crop` | Phone-shaped clips: blurred background (default) or center crop. |
| `--skip START-END` | Ignore a time range. Repeatable. |
| `--count N` | Make exactly the best N clips instead of auto mode. |
| `--min-gap SEC` | With `--count`: minimum seconds between clips (default 60). |
| `--all` | Tile the whole video: as many non-overlapping clips as fit, good or not. |
| `--use-video` | Also score visual action (scene changes). Slower. |
| `--reencode` | With `--landscape`: re-encode for frame-accurate cuts (the default there is fast stream copy, which can start a second or two off). |
| `--out FOLDER` | Output folder (default `clips`). |

If you get too many or too few clips, adjust `--quality`. When it finishes it prints the ratio it
ended up with, e.g. "about 1 clip per 1.2 min".

## How it works

1. FFmpeg extracts the audio (mono, 16 kHz).
2. Every half-second gets a score from how loud it is compared to the 30 seconds around it, plus
   how sudden the jump is.
3. Near-silent stretches (AFK, dead air) are ignored.
4. A moment is kept if its score is `--quality` robust standard deviations above the video's
   typical score. Strongest moments are placed first and clips never overlap.
5. FFmpeg cuts each clip out of the original; the original file is never modified.

It judges moments by **audio energy**, so it's best on content where exciting things are loud
(gaming, streams, reactions, sports). It can't tell *what* is being said or shown. Quiet-but-great
moments can be missed, and loud-but-boring moments can be picked.

## Roadmap ideas

- Transcript-based picking (Whisper) so clips can follow what is said
- Automatic captions
- Facecam/gameplay layouts for vertical clips

Issues and pull requests are welcome.

## License

[MIT](LICENSE): free to use, change and share.
