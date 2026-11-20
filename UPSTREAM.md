# Upstream

CLIPPER's pipeline is taken from [jipraks/yt-short-clipper](https://github.com/jipraks/yt-short-clipper)
at commit `f6d6d98f3bcee1b056cd49990a7f7176c55ac10b` (2026-09-20), licensed MIT,
Copyright (c) 2026 Aji Prakoso (jipraks). The original `LICENSE` is kept unchanged in
this repo, as the license requires.

## Kept (copied into `clipper/`)

`caption_generator.py`, `clip_processor.py`, `constants.py`, `cookies.py`, `gpu.py`,
`helpers.py`, `highlight_finder.py`, `hook_generator.py`, `json3_parser.py`,
`portrait.py`, `prompts.py`, `session.py`, `srt_parser.py`, `subtitle_downloader.py`,
`subtitle_fetcher.py`, `video_processor.py`, `watermark.py`, `__init__.py`.

## Dropped

- The Tauri/Rust shell and the React UI (`src-tauri/`, `src/`, `public/`, `package.json`, ...)
- `sidecar.py`: the JSON-lines protocol the GUI used to call the Python core
- `repliz.py`: upload to the third-party Repliz service
- `portrait_run.py`, `scripts/*.ps1`, `build/`: PyInstaller and Windows packaging
- The in-app AI wallet, account/top-up code, and the Google Analytics telemetry
  (all of which lived in the Rust/React layers, not in the Python core)

## Added by CLIPPER

- `clipper/cli.py`, `clipper/__main__.py`: the headless `clipper` command
  (`doctor`, `find`, `process`, `run`, `social`, `review`, `export`, `eval`) calling upstream's
  `find_highlights_only` and `process_selected_highlights` directly
- `clipper/config.py`: `.env` / environment configuration. Claude by default (key starts with `sk-ant-`), or any
  OpenAI-compatible endpoint when `CLIPPER_BASE_URL` names one; a Claude key is only ever sent to Anthropic
- `clipper/windowing.py`: overlapping windows, de-duplication and cross-window re-scoring for long videos
- `clipper/viral_prompt.py`, `clipper/profiles.py`: the default `viral` highlight prompt, its scoring
  rubric, and the `viral` / `podcast` profile choice (the `podcast` profile is upstream's prompt, unchanged)
- `clipper/social.py`: title, three caption angles, hashtags, search keywords and a pinned comment per clip
  (replaces a generator that lived in upstream's dropped desktop-app glue, `sidecar.py`)
- `clipper/trend_research.py`: researches what is trending with a web-search-enabled model, keeps only
  items matched to the sources the search returned (when the route can return them), cleans the text, caches
  the result, and backs off for a day after a failure (TikTok has no official trends feed)
- `clipper/trends.py`: an optional user-written trends file, which takes priority over the research
- `clipper/review.py`, `clipper/export.py`, `clipper/evaluation.py`: review sheet, export folder (each clip also gets a matching `.txt` with its captions and hashtags), and
  agreement between the model's scores and the user's ratings
- `clipper/claude_client.py`: Claude through Anthropic's official `anthropic` SDK, and the only module that imports it. No
  `temperature` (current Claude models reject it), room to think added to `max_tokens`, text blocks only, refusals and
  running out of room reported as errors, every API error explained in plain words
- `clipper/llm.py`: a small chat helper for the calls above; sends each to Claude or to an OpenAI-compatible endpoint
- `clipper/encoding.py`: one choice of video encoder for every step that writes a video (the section cut, the portrait
  layout, the hook, the captions, the watermark): the computer's hardware encoder when a test encode shows it works,
  otherwise libx264 `veryfast`; a hardware encoder that fails is replaced by libx264 and not tried again that run. Also
  the frame-rate cap (`CLIPPER_MAX_FPS`) and the source height (`CLIPPER_MAX_HEIGHT`)
- `clipper/drama.py`: what counts as a drama scene (a separate AI judgement of 6+ out of 10), the 6-of-8 bar,
  the drama-first ranking, and the report behind `clipper check`. `windowing.py` does the judging and the
  second look for confrontations; `viral_prompt.py` carries the "stop-the-thumb" rules
- `clipper/diagnostics.py`: the facts printed when a download dies with a cryptic file error (certifi's
  certificate file, cookies, free disk space), and a repair for a stale remembered certificate path. The CLI
  uses it so that one failed clip no longer stops the others (`cli.render_clips`)
- `clipper/gui/`, `CLIPPER.command`: the point-and-click app (`clipper gui`): a small local web server on 127.0.0.1
  (standard library only) that runs the same `clipper` commands and shows progress, the moments found, finished
  clips with copy buttons for captions and hashtags, and a setup check. `CLIPPER.command` starts it by double-click
- `clipper/trend_feeds.py`, `trend_sources.py`, `trend_rank.py`, `trend_sites.py`, `trend_net.py`: live trends, read on
  their own from public sources (not TikTok), cross-checked, driven by the niche and the video, plus websites you
  list in `trend_sites.txt`. `trend_net.py` is the only place they touch the internet
- `pyproject.toml`, `requirements.txt` (pinned versions for dependency scanners), `setup.sh`, `.env.example`, `tests/`

## Modified upstream code

- `clipper/video_processor.py`: `_get_cookies_path()` first honours `$CLIPPER_COOKIES`,
  so the section download uses the cookies file the CLI validated. The yt-dlp options also get
  `verbose` when `$CLIPPER_YTDLP_VERBOSE` is set, which shows yt-dlp's debug output and error tracebacks.
- `clipper/portrait.py`: MediaPipe is imported lazily inside `convert_to_portrait()`
  (with an install hint on failure) instead of at module level. Upstream's module-level
  import made MediaPipe a hard requirement for the whole package, but its current
  releases have no macOS Intel wheels, so such installs failed outright.
- `clipper/session.py`: `find_highlights_only()` calls `find_highlights_windowed()` instead of
  `find_highlights()` directly. For a short video that is the same single call as before. It also passes
  the `provider` and `effort` settings along (see the fourth `highlight_finder.py` change).
- `clipper/highlight_finder.py`: **bug fix.** The `--direction` block and its reminder contain
  `{output_language}`, but upstream inserted them after that placeholder had already been replaced, so the
  model saw the literal text `{output_language}` whenever a direction was used. They are now filled in before
  insertion.
- `clipper/highlight_finder.py` (second change): the 58 and 120 second limits are now the named constants
  `MIN_CLIP_SECONDS` and `MAX_CLIP_SECONDS`, and `find_highlights()` takes an optional `fit_length` function.
  When it is given, picks shorter than 58 s are widened with the surrounding transcript before the usual
  length check instead of being discarded (`windowing.fit_to_length` does the widening). Without it nothing
  changes. The check for invalid timestamps also catches `TypeError`/`AttributeError`, so a model that writes
  a time as a number gets that one pick skipped instead of crashing the run.
- `clipper/highlight_finder.py` (third change): `find_highlights()` and `_validate_highlights()` take an
  optional `keep_all`. Upstream keeps the first `num_clips` valid picks in the model's own order, which threw
  away candidates before anything ranked them (6 of 22 on a real run); with `keep_all` every valid pick is
  returned so the caller can rank them all. Without it nothing changes.
- `clipper/highlight_finder.py` (fourth change): `find_highlights()` takes optional `provider` and `effort`. For Claude
  (`provider="anthropic"`, or a key that starts with `sk-ant-`) the same prompt goes to `claude_client.message()` and no
  temperature is sent. Upstream's request to an OpenAI-compatible endpoint is moved, line for line, into
  `_ask_openai_compatible()` so both routes feed the same parsing and validation. Without the new arguments, for a
  non-Claude key, nothing changes.
- `clipper/subtitle_downloader.py`: the saved video info also keeps the video's own `tags`, which feed the hashtag
  suggestions. Nothing else about the download changed.
- Rendering speed. A clip cost a MacBook Air an estimated 20 minutes (yt-dlp's section cut re-encoded the whole section at
  ffmpeg's defaults while reporting nothing, then three more full encodes on the CPU):
  - `clipper/gpu.py`: the result is remembered for the run; on any Mac the encoder is found by testing VideoToolbox, not by
    recognising the graphics card's name (upstream matched only Apple silicon chip names, so Intel Macs always got the CPU);
    VideoToolbox is given a bitrate (without one it writes about 200 kbit/s); the test encode takes the encoder's real settings.
  - `clipper/portrait.py`: the blurred background is blurred at a quarter of its size and enlarged (16x fewer pixels, SSIM 0.998
    against the full-size blur); a source faster than the cap is thinned first; the encoder comes from `encoding.py`.
  - `clipper/hook_generator.py`, `clipper/caption_generator.py`, `clipper/watermark.py`: `libx264 -preset fast -crf 18` replaced
    by the shared encoder choice.
  - `clipper/video_processor.py`: the section cut passes ffmpeg fast encoder settings (the cut stays frame-exact, which the
    caption timing needs); the format choice takes a `max_height`; if ffmpeg rejects the settings it cuts once more with its
    defaults before the last resort; the "waiting for download data" message no longer claims what it cannot know.
  - `clipper/clip_processor.py`: asks for 720 lines in the centered layout and prints a `Timing:` line per clip.
- `clipper/constants.py`: one docstring now names the new package.
- `clipper/helpers.py`: a `# nosec B310` comment on the one-time face-model download, which uses a
  fixed https URL, so the security scanner `bandit` does not flag it. No behaviour change.
- The package was renamed from `yt_short_clipper_core` to `clipper`. All upstream
  imports inside the core are relative, so no other code changed.

## Known upstream behaviour (not yet changed here)

- No speech-to-text. Highlights and word-by-word captions both come from YouTube's own
  caption tracks, so a video without captions fails with "No subtitle available".
- Clips are kept only if they run 58-120 s (hard-coded in `_validate_highlights`).
- Downloads need a logged-in YouTube `cookies.txt`.

## History

Commit `99c1e97` on this branch imported a different project,
[op7418/Youtube-clipper-skill](https://github.com/op7418/Youtube-clipper-skill) (MIT, a Claude Code
skill), as the first base. It was replaced by the jipraks core in the commit after it;
the earlier commit remains in git history.
