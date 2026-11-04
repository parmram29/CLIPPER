# CLIPPER

Turn a long video into vertical 9:16 short-form clips: an LLM picks the highlights from the
transcript, then each one is cut, reframed, captioned word by word, and given a hook overlay.

CLIPPER is a headless CLI built on the pipeline from
[jipraks/yt-short-clipper](https://github.com/jipraks/yt-short-clipper) (MIT). See
[UPSTREAM.md](UPSTREAM.md) for exactly what was kept, dropped, and changed.

## Setup

Needs Python 3.10+ and `ffmpeg` with libass on your PATH (macOS: `brew install ffmpeg`).

```bash
bash setup.sh
source .venv/bin/activate
```

**Face tracking is optional.** It needs MediaPipe, which `setup.sh` tries to install and skips
if there is no build for your machine. Without it, `--reframe auto` (the default) uses a centered
crop and everything else works. On Debian/Ubuntu, face tracking also needs
`sudo apt install libegl1 libgles2`. `clipper doctor` reports both cases.

Then edit `.env`:

| Variable | Meaning |
|---|---|
| `CLIPPER_API_KEY` | key for any OpenAI-compatible endpoint |
| `CLIPPER_BASE_URL` | default: Alibaba Model Studio (Qwen), international endpoint |
| `CLIPPER_MODEL` | default: `qwen-plus` |
| `CLIPPER_COOKIES` | path to a YouTube `cookies.txt` (default `./cookies.txt`) |

Alibaba keys are region-specific; for a Beijing key use
`https://dashscope.aliyuncs.com/compatible-mode/v1`
([docs](https://www.alibabacloud.com/help/en/model-studio/text-generation)).
`cookies.txt` and `.env` are git-ignored. Never commit them.

```bash
clipper doctor --check-ai    # verifies ffmpeg, libraries, cookies, and makes one tiny AI call
```

## Use

```bash
clipper find "https://www.youtube.com/watch?v=..." --clips 5     # pick highlights, writes a session
clipper process out/sessions/<session> --top 3                   # render the 3 best
clipper run "https://www.youtube.com/watch?v=..." --top 3        # both steps at once
```

Useful options:

- `--direction "skip the intro; use 12:30 - 13:40"` steers the highlight model. An explicit clock range is used exactly.
- `--prompt-file niche.txt` replaces the highlight prompt (must contain `{transcript}` and `{num_clips}`). Start from `clipper/prompts.py`.
- `--reframe auto|face|centered|original` and `--background black|blurred`. `auto` (default) tracks faces if MediaPipe is installed and centers otherwise; an explicit `face` without MediaPipe fails before downloading anything. Face tracking is the slow path; centered is a single ffmpeg pass.
- `--pick 1,3-4` or `--top N` chooses which clips to render. Rendered clips are skipped on re-run.

Clips land in `<session>/clips/clip_NNN/master.mp4` with a `data.json` (title, description, hook, timings).

## Status and limits

- **Tested (`pytest`, no network except the one-time face-model download):** config handling, the CLI, highlight selection against a real local OpenAI-compatible server, session writing, the centered reframe, caption and hook stages on a synthetic video, and face-tracked reframing on a real face photo (a plain center crop loses the face; the tracked crop keeps it).
- **Not tested:** an actual YouTube download, a live Qwen call (run `clipper doctor --check-ai`), and face tracking on moving video with several people.
- **Needs YouTube captions.** There is no speech-to-text fallback yet, so a video without a caption track fails. Word-by-word captions also come from YouTube's track.
- **Whole transcript in one prompt.** Very long videos can exceed the model's context window.
- **The default prompt is tuned for podcasts** (conflict, confession, punchlines). Use `--prompt-file` per niche.
- **Cookies are your logged-in session.** Treat the file like a password.
- Only clip footage you have the rights to use. YouTube's terms and copyright law apply.

## Troubleshooting

**`Could not find a version that satisfies the requirement mediapipe` (`from versions: none`)**
MediaPipe's current releases (1.x) publish macOS wheels for **Apple Silicon only**; the last
release with Intel-Mac wheels is 0.10.21, for Python 3.9-3.12
([PyPI files](https://pypi.org/project/mediapipe/#files)). So an Intel Mac, or a Python running under
Rosetta, on Python 3.13+ has nothing to install. Check yours:

```bash
python3 -c "import sys, platform; print(sys.version.split()[0], platform.machine())"
```

`x86_64` means Intel (or Rosetta); `arm64` means Apple Silicon. Current `setup.sh` no longer fails on this
and runs without face tracking. To get face tracking on Intel, use Python 3.9-3.12 in a fresh venv
(e.g. `brew install python@3.12`) and `pip install 'clipper[face]'`.

## Tests

```bash
pytest
```

The face-tracking tests are skipped when MediaPipe isn't installed.
