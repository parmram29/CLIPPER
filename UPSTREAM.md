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
- `clipper/config.py`: `.env` / environment configuration for any OpenAI-compatible endpoint
- `clipper/windowing.py`: overlapping windows, de-duplication and cross-window re-scoring for long videos
- `clipper/viral_prompt.py`, `clipper/profiles.py`: the default `viral` highlight prompt, its scoring
  rubric, and the `viral` / `podcast` profile choice (the `podcast` profile is upstream's prompt, unchanged)
- `clipper/social.py`: title, three caption angles, hashtags, search keywords and a pinned comment per clip
  (replaces a generator that lived in upstream's dropped desktop-app glue, `sidecar.py`)
- `clipper/trends.py`: reads a user-maintained file of what is trending, since TikTok has no official trends feed
- `clipper/review.py`, `clipper/export.py`, `clipper/evaluation.py`: review sheet, export folder, and
  agreement between the model's scores and the user's ratings
- `clipper/llm.py`: a small chat helper for the calls above
- `pyproject.toml`, `setup.sh`, `.env.example`, `tests/`

## Modified upstream code

- `clipper/video_processor.py`: `_get_cookies_path()` first honours `$CLIPPER_COOKIES`,
  so the section download uses the cookies file the CLI validated.
- `clipper/portrait.py`: MediaPipe is imported lazily inside `convert_to_portrait()`
  (with an install hint on failure) instead of at module level. Upstream's module-level
  import made MediaPipe a hard requirement for the whole package, but its current
  releases have no macOS Intel wheels, so such installs failed outright.
- `clipper/session.py`: `find_highlights_only()` calls `find_highlights_windowed()` instead of
  `find_highlights()` directly. For a short video that is the same single call as before.
- `clipper/highlight_finder.py`: **bug fix.** The `--direction` block and its reminder contain
  `{output_language}`, but upstream inserted them after that placeholder had already been replaced, so the
  model saw the literal text `{output_language}` whenever a direction was used. They are now filled in before
  insertion.
- `clipper/constants.py`: one docstring now names the new package.
- The package was renamed from `yt_short_clipper_core` to `clipper`. All upstream
  imports inside the core are relative, so no other code changed.

## Known upstream behaviour (not yet changed here)

- No speech-to-text. Highlights and word-by-word captions both come from YouTube's own
  caption tracks, so a video without captions fails with "No subtitle available".
- Clips are kept only if they run 58-120 s (hard-coded in `_validate_highlights`).
- Downloads need a logged-in YouTube `cookies.txt`.

## History

Commit `cb068fb` on this branch imported a different project,
[op7418/Youtube-clipper-skill](https://github.com/op7418/Youtube-clipper-skill) (MIT, a Claude Code
skill), as the first base. It was replaced by the jipraks core in the commit after it;
the earlier commit remains in git history.
