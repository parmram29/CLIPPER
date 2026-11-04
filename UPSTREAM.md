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
  (`doctor`, `find`, `process`, `run`) calling upstream's `find_highlights_only` and
  `process_selected_highlights` directly
- `clipper/config.py`: `.env` / environment configuration for any OpenAI-compatible endpoint
- `pyproject.toml`, `setup.sh`, `.env.example`, `tests/`

## Modified upstream code

- `clipper/video_processor.py`: `_get_cookies_path()` first honours `$CLIPPER_COOKIES`,
  so the section download uses the cookies file the CLI validated.
- `clipper/constants.py`: one docstring now names the new package.
- The package was renamed from `yt_short_clipper_core` to `clipper`. All upstream
  imports inside the core are relative, so no other code changed.

## Known upstream behaviour (not yet changed here)

- No speech-to-text. Highlights and word-by-word captions both come from YouTube's own
  caption tracks, so a video without captions fails with "No subtitle available".
- The whole transcript is sent to the model in one prompt, with no chunking.
- The default prompt targets podcast footage and a 58-120 s clip length.
- Downloads need a logged-in YouTube `cookies.txt`.

## History

Commit `cb068fb` on this branch imported a different project,
[op7418/Youtube-clipper-skill](https://github.com/op7418/Youtube-clipper-skill) (MIT, a Claude Code
skill), as the first base. It was replaced by the jipraks core in the commit after it;
the earlier commit remains in git history.
