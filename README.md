# AutoClip

Free, local auto-clipper. Point it at a long video (a stream, a gameplay session, a podcast) and it
finds the moments worth sharing and cuts each one into its own **phone-ready clip** for YouTube
Shorts, TikTok and Reels. Nothing is uploaded anywhere: it runs on your own computer with FFmpeg
and a little Python.

- Scans the **whole video** and keeps every moment that spikes above what is around it (yelling,
  laughing, screaming, explosions, jump scares, big reactions). More eventful video = more clips;
  dead air = none.
- **Tight clips.** Each one starts a couple of seconds before the action and ends right after the
  payoff. No long lead-in, no dead air at the start, no trailing chatter. Length follows the moment
  (10-45 s by default) instead of a fixed 30 s.
- **Phone-shaped output** (9:16, 1080x1920) with the full picture over a blurred background,
  placed where phone apps cover the least. Widescreen, portrait and rotated phone footage all work.
- **Platform-ready export**: H.264 + AAC 48 kHz, frame rate and bitrate capped per platform, files
  kept under TikTok/Reels size limits, audio leveled to about -14 LUFS.
- Optional `--skip` ranges to ignore an intro, a break, or anything after a certain time.
- Clips never overlap and are named `clip001.mp4`, `clip002.mp4`, ... in video order, with a
  `clips_list.txt` of each clip's time in the original video.

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

### Pick a platform

```
python autoclip.py stream.mp4 --platform shorts   # default
python autoclip.py stream.mp4 --platform tiktok
python autoclip.py stream.mp4 --platform reels
```

| Platform | Clip length | Frame rate | Bitrate / size |
|---|---|---|---|
| `shorts` | 10-45 s | up to 60 fps | up to 16 Mbps |
| `tiktok` | 10-45 s | up to 30 fps | up to 15 Mbps, under 50 MB |
| `reels` | 8-30 s | up to 30 fps | up to 12 Mbps, under 50 MB |

Override the length range with `--min-length` and `--max-length`, or force one exact length with
`--length 30`.

### Too many or too few clips?

Adjust `--quality` (default `3`). It is how far a moment must stand out from the rest of the video:

```
python autoclip.py stream.mp4 --quality 2     # looser: more clips
python autoclip.py stream.mp4 --quality 4     # pickier: only the strongest
```

When it finishes it prints the ratio it ended up with, e.g. "about 1 clip per 1.2 min". Want a hard
limit instead? `--per 3` means at most one clip per 3 minutes of video on average, and `--count 15`
makes exactly the best 15.

### Skip parts of the video

```
python autoclip.py stream.mp4 --skip 0-10:00 --skip 1:20:00-1:35:00 --skip 2:00:00-end
```

Times are seconds (`90`), `M:SS` (`5:00`) or `H:MM:SS` (`1:20:00`). Use `start` / `end` as range
ends. A skip that starts after the end of the video is ignored with a warning.

### Other shapes

`--style crop` zooms into the center of the picture instead of fitting it over a blur (cuts off the
sides). `--landscape` keeps the original wide shape and uses fast, lossless cuts.

## Options

| Option | What it does |
|---|---|
| `--platform shorts\|tiktok\|reels` | Export preset: clip length range, frame rate, bitrate and size limits (default `shorts`). |
| `--quality N` | How much a moment must stand out (default `3`). Lower = more clips, higher = fewer. |
| `--per MIN` | Cap at one clip per `MIN` minutes of video on average. |
| `--count N` | Make exactly the best N clips instead of auto mode. |
| `--all` | Tile the whole video with fixed-length clips, good or not (`--length`, default 30 s). |
| `--length SEC` | Force every clip to this length instead of fitting each moment. |
| `--min-length SEC` / `--max-length SEC` | Shortest / longest clip (defaults come from `--platform`). |
| `--min-gap SEC` | Breathing room kept around each clip (default `3`). |
| `--skip START-END` | Ignore a time range. Repeatable. |
| `--style blur\|crop` | Phone-shaped clips: blurred background (default) or center crop. |
| `--landscape` | Keep the original shape instead of 9:16. |
| `--no-normalize` | Don't level the audio to about -14 LUFS. |
| `--use-video` | Also score visual action (scene changes). Slower. |
| `--reencode` | With `--landscape`: re-encode for frame-accurate cuts. |
| `--out FOLDER` | Output folder (default `clips`). |

## How it picks clips

Short-form feeds are won or lost in the first seconds, and a clip only travels if a stranger gets
the stakes with no backstory. So AutoClip looks for what makes those moments:

1. FFmpeg extracts the audio (mono, 16 kHz) and splits it into half-second windows.
2. Each window gets an **arousal score** that measures how much it stands out from the surrounding
   30 seconds: a sudden jump in loudness, new sound appearing (spectral flux), and a brighter,
   higher-pitched sound like shouting, laughing or screaming. Loud-but-constant stretches, like
   background music, score low because nothing is changing.
3. Near-silent stretches (AFK, menus, dead air) are ignored, along with the moment you come back
   from them.
4. A moment is kept if its score is `--quality` robust standard deviations above the video's
   typical score. Strongest moments claim their spot first.
5. Each moment is cut to its own length: it starts about 2.5 s before the action begins and ends
   about 2.5 s after it dies down, any dead air is trimmed off both ends, and very short moments
   get a little extra context before them.
6. FFmpeg cuts the clip out of the original, reframes it to 9:16 and exports it. The original file
   is never modified.

It judges moments by **audio energy**, so it works best on content where exciting things are loud
(gaming, streams, reactions, sports, horror co-op). It can't tell *what* is being said or shown.
Quiet-but-great moments can be missed, and loud-but-boring moments can be picked, so skim the clips
and tune `--quality` for your content.

## Tips from the pros

- Review clips on an actual phone before posting. Keep captions and facecams out of the bottom
  quarter of the screen and away from the right edge, where apps put buttons and text.
- Shorter finished clips usually beat longer ones: aim to end on the payoff.
- Cross-posting? Run once per platform (`--platform tiktok`, `--platform reels`) so each gets its
  own length, frame rate and file size.

## Roadmap ideas

- Transcript-based picking (Whisper) so clips can follow what is said
- Automatic short captions
- Facecam-on-top / gameplay-below layout for vertical clips

Issues and pull requests are welcome.

## License

[MIT](LICENSE): free to use, change and share.
