"""Headless command-line interface for CLIPPER.

    clipper doctor [--check-ai]
    clipper find URL [--clips N] [--profile viral|podcast] [--direction TEXT] ...
    clipper process SESSION_DIR [--pick 1,3-4 | --top N] [--reframe ...] [--export] ...
    clipper run URL [--clips N] [--top N] ...
    clipper social SESSION_DIR      write a title, 3 caption angles and hashtags for each clip
    clipper trends [--refresh]      the trend list the captions draw on (web research is experimental, off by default)
    clipper review SESSION_DIR      write a review sheet (CSV) to rate the picks
    clipper export SESSION_DIR      gather finished clips into one folder (for AirDrop)
    clipper eval review.csv         compare the model's scores with your ratings

Not part of the upstream yt-short-clipper code, which drives these same pipeline
functions from a desktop GUI over a JSON-lines sidecar protocol.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from . import config, profiles
from .windowing import DEFAULT_OVERLAP_SECONDS, DEFAULT_WINDOW_SECONDS

DEFAULT_OUT = "out"
DEFAULT_TOP = 3
# Without {transcript} the model never sees the video; without {num_clips} the
# count it is asked for is undefined. Both are filled in by str.replace upstream.
REQUIRED_PROMPT_PLACEHOLDERS = ("{transcript}", "{num_clips}")
FACE_INSTALL_HINT = (
    "Face tracking needs MediaPipe, which is not installed. Install it with "
    "`pip install 'clipper[face]'` (it has no build for every Python/CPU, e.g. Intel "
    "Macs on Python 3.13+), or use `--reframe centered`."
)


def _log(message: str) -> None:
    print(message, flush=True)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested; no network, no heavy imports)
# ---------------------------------------------------------------------------


def parse_pick(spec: str, total: int) -> list[int]:
    """Turn a 1-based selection like ``"1,3-4"`` into 0-based indices.

    Order is preserved and duplicates are dropped. Raises ValueError on
    unreadable or out-of-range input.
    """
    picked: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                lo_text, hi_text = part.split("-", 1)
                lo, hi = int(lo_text), int(hi_text)
            else:
                lo = hi = int(part)
        except ValueError:
            raise ValueError(f"cannot read {part!r} as a clip number or range") from None
        if lo > hi:
            raise ValueError(f"empty range {part!r}")
        for n in range(lo, hi + 1):
            if not 1 <= n <= total:
                raise ValueError(f"clip {n} is out of range (this session has 1-{total})")
            if n - 1 not in picked:
                picked.append(n - 1)
    if not picked:
        raise ValueError("no clips selected")
    return picked


def _score(highlight: Mapping[str, Any]) -> float:
    try:
        return float(highlight.get("virality_score", 0))
    except (TypeError, ValueError):
        return 0.0


def top_indices(highlights: list[Mapping[str, Any]], n: int) -> list[int]:
    """Indices of the ``n`` highest-scoring highlights, best first (ties keep order)."""
    if n < 1:
        raise ValueError("--top must be at least 1")
    order = sorted(range(len(highlights)), key=lambda i: (-_score(highlights[i]), i))
    return order[:n]


_CA_FALLBACK: str | None = None


def ensure_ca_certificates() -> str | None:
    """Point Python's TLS at certifi's CA bundle when it has no usable one of its own.

    Python installed from python.org on macOS ships without root certificates, so every
    HTTPS request (yt-dlp's included) fails with "CERTIFICATE_VERIFY_FAILED: unable to
    get local issuer certificate". The usual fix is a one-off `Install Certificates.command`.
    This does the equivalent automatically, but never overrides an explicit SSL_CERT_FILE /
    SSL_CERT_DIR, and does nothing when the interpreter already has a bundle.

    Returns the bundle path if it was applied, else None.
    """
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return None
    paths = ssl.get_default_verify_paths()  # cafile/capath are None when the path is missing
    if paths.cafile or paths.capath:
        return None
    try:
        import certifi
    except ImportError:
        return None
    os.environ["SSL_CERT_FILE"] = certifi.where()
    return certifi.where()


def mediapipe_available() -> bool:
    """True if MediaPipe is installed (checked without importing it)."""
    try:
        return importlib.util.find_spec("mediapipe") is not None
    except (ImportError, ValueError):
        return False


def resolve_reframe(mode: str, available: bool | None = None) -> str:
    """Turn ``auto`` into ``face`` or ``centered``; reject ``face`` without MediaPipe.

    Failing here, before any download, beats failing after minutes of work.
    """
    available = mediapipe_available() if available is None else available
    if mode == "auto":
        return "face" if available else "centered"
    if mode == "face" and not available:
        raise RuntimeError(FACE_INSTALL_HINT)
    return mode


def load_session(session_dir: str | Path) -> dict[str, Any]:
    path = Path(session_dir) / "session_data.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Pass a session folder created by `clipper find` "
            f"(look under {DEFAULT_OUT}/sessions/)."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def select_highlights(
    session: Mapping[str, Any],
    *,
    pick: str | None = None,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """Choose highlights from a session; default is all of them.

    Each returned copy carries ``_highlight_index`` (its position in the session),
    which the upstream processor uses to name ``clip_NNN`` folders and to skip
    clips that were already rendered.
    """
    highlights = session.get("highlights") or []
    if not highlights:
        raise ValueError("this session has no highlights")
    if pick:
        indices = parse_pick(pick, len(highlights))
    elif top is not None:
        indices = top_indices(highlights, top)
    else:
        indices = list(range(len(highlights)))
    chosen = []
    for i in indices:
        h = dict(highlights[i])
        h["_highlight_index"] = i
        chosen.append(h)
    return chosen


def build_options(*, reframe: str, background: str, captions: bool, hook: bool) -> dict[str, Any]:
    """The ``options`` dict upstream's ``process_selected_highlights`` reads."""
    return {
        "reframeMode": reframe,
        "centeredBackground": background,
        "addCaptions": captions,
        "addHook": hook,
        "addWatermark": False,
        "addCreditWatermark": False,
    }


def load_prompt_file(path: str | Path) -> str:
    text = Path(path).read_text(encoding="utf-8")
    missing = [p for p in REQUIRED_PROMPT_PLACEHOLDERS if p not in text]
    if missing:
        raise ValueError(
            f"prompt file {path} is missing required placeholder(s): {', '.join(missing)}. "
            "Without {transcript} the model never sees the video."
        )
    return text


def format_highlights(highlights: list[Mapping[str, Any]]) -> str:
    lines = []
    for i, h in enumerate(highlights, 1):
        lines.append(
            f"{i:>2}. [{h.get('virality_score', '?')}/10] "
            f"{h.get('start_time', '?')} -> {h.get('end_time', '?')} "
            f"({h.get('duration_seconds', '?')}s)  {h.get('title', 'Untitled')}"
        )
        if h.get("hook_text"):
            lines.append(f"      hook: {h['hook_text']}")
    return "\n".join(lines)


def resolve_cookies(cli_value: str | None, env: Mapping[str, str] | None = None) -> str:
    """Validate the cookies file and export it as CLIPPER_COOKIES.

    The section downloader discovers cookies on its own (cwd / app dir), so
    exporting the resolved path makes every stage use the file that was validated.
    """
    env = os.environ if env is None else env
    candidate = cli_value or env.get("CLIPPER_COOKIES") or "cookies.txt"
    from .cookies import validate_cookies

    ok, error = validate_cookies(candidate)
    if not ok:
        raise config.ConfigError(f"{error}\n(looked for: {candidate})")
    resolved = str(Path(candidate).resolve())
    os.environ["CLIPPER_COOKIES"] = resolved
    return resolved


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def prompt_and_window_settings(args: argparse.Namespace) -> dict[str, Any]:
    """The highlight prompt plus the long-video windowing options, as the ``ai`` dict keys
    upstream's session code reads."""
    if args.window_minutes <= 0 or args.overlap_minutes < 0 or args.overlap_minutes >= args.window_minutes:
        raise ValueError("--window-minutes must be positive and larger than --overlap-minutes")
    return {
        # --prompt-file wins over --profile
        "system_message": (
            load_prompt_file(args.prompt_file)
            if args.prompt_file
            else profiles.get_profile_prompt(args.profile)
        ),
        "window_seconds": args.window_minutes * 60,
        "overlap_seconds": args.overlap_minutes * 60,
        "rerank": not args.no_rerank,
    }


def _find(args: argparse.Namespace) -> dict[str, Any]:
    ai = config.load_ai_config().as_dict()
    ai.update(prompt_and_window_settings(args))
    cookies = resolve_cookies(args.cookies)

    from .session import find_highlights_only

    session = find_highlights_only(
        url=args.url,
        num_clips=args.clips,
        subtitle_language=args.sub_lang,
        output_dir=args.out,
        cookies_path=cookies,
        ai=ai,
        user_direction=args.direction,
        output_language=args.out_language,
        log=_log,
    )
    print("\nHighlights:")
    print(format_highlights(session["highlights"]))
    return session


def _process(
    session_dir: str | Path,
    *,
    cookies: str | None,
    pick: str | None,
    top: int | None,
    reframe: str,
    background: str,
    captions: bool,
    hook: bool,
    lead_in: float | None,
    tail_out: float | None,
    social: bool = True,
    export: bool = False,
    export_to: str | None = None,
    open_after: bool = False,
    niche: str | None = None,
    trends_file: str | None = None,
    region: str | None = None,
    auto_trends: bool = True,
    refresh_trends: bool = False,
) -> dict[str, Any]:
    session = load_session(session_dir)
    chosen = select_highlights(session, pick=pick, top=top)
    # Read these before rendering, so a mistyped --trends path fails now, not after the render.
    niche_text = resolve_niche(niche)
    trends = (
        load_trends_for_run(
            trends_file, niche=niche_text, region=region, auto=auto_trends, refresh=refresh_trends
        )
        if social
        else None
    )
    requested_reframe = reframe
    reframe = resolve_reframe(requested_reframe)
    if requested_reframe == "auto" and reframe == "centered":
        _log("MediaPipe not installed: using --reframe centered (no face tracking)")
    resolve_cookies(cookies)

    from .clip_processor import process_selected_highlights

    result = process_selected_highlights(
        url=session["url"],
        highlights=chosen,
        session_dir=str(session_dir),
        options=build_options(
            reframe=reframe, background=background, captions=captions, hook=hook
        ),
        # None falls back to upstream's defaults (1.5s lead-in, 2.5s tail-out).
        ai={"clip_padding": {"lead_in": lead_in, "tail_out": tail_out}},
        log=_log,
    )
    print("\nClips:")
    for r in result["results"]:
        note = "  (already rendered, skipped)" if r.get("skipped") else ""
        print(f"  {r['output_path']}{note}")

    if social:
        _generate_social(session_dir, session, chosen, niche=niche_text, trends=trends)
    if export or export_to or open_after:
        _export(session_dir, export_to, open_after)
    return result


def resolve_niche(cli_value: str | None, env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    return (cli_value or env.get("CLIPPER_NICHE") or "").strip() or None


def resolve_region(cli_value: str | None, env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    return (cli_value or env.get("CLIPPER_REGION") or "").strip() or None


def auto_trends_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Automatic trend research is experimental and OFF unless ``CLIPPER_AUTO_TRENDS`` is switched on.

    On the one account it was tried on, Alibaba's search returned only Chinese-language pages about
    TikTok in general, so by default nothing is researched and no failed attempts clutter a run.
    """
    env = os.environ if env is None else env
    return (env.get("CLIPPER_AUTO_TRENDS") or "").strip().lower() in ("1", "true", "yes", "on")


def search_ai_config(env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """The AI settings for trend research: same key and endpoint, optionally a different model
    (``CLIPPER_SEARCH_MODEL``) in case the chat model does not support web search."""
    env = os.environ if env is None else env
    try:
        ai = config.load_ai_config(env).as_dict()
    except config.ConfigError:
        return None
    ai["model"] = (env.get("CLIPPER_SEARCH_MODEL") or "").strip() or ai["model"]
    return ai


def search_api(env: Mapping[str, str] | None = None) -> str:
    """Which route to use for web search: ``dashscope`` (Alibaba's native API) or ``compat``.

    ``CLIPPER_SEARCH_API`` can force either. Left unset, an Alibaba endpoint uses the native API,
    which returns the sources a search used (the OpenAI-compatible endpoint never does, per
    Alibaba's docs) and was confirmed to run searches on a real account; any other provider uses
    the OpenAI-compatible route.
    """
    from urllib.parse import urlparse

    env = os.environ if env is None else env
    value = (env.get("CLIPPER_SEARCH_API") or "auto").strip().lower() or "auto"
    if value not in ("auto", "compat", "dashscope"):
        raise config.ConfigError(f"CLIPPER_SEARCH_API must be 'auto', 'compat' or 'dashscope', not {value!r}")
    if value != "auto":
        return value
    host = urlparse(env.get("CLIPPER_BASE_URL") or config.DEFAULT_BASE_URL).hostname or ""
    return "dashscope" if host.endswith("aliyuncs.com") else "compat"


def load_trends_for_run(
    cli_value: str | None,
    env: Mapping[str, str] | None = None,
    *,
    niche: str | None = None,
    region: str | None = None,
    auto: bool = True,
    refresh: bool = False,
):
    """Work out which trends the captions should use, and say what was found.

    In order: a file you named (``--trends`` / ``$CLIPPER_TRENDS``), then ``./trends.txt`` if you
    have filled it in, then automatic web research (cached, refreshed every few days). A path
    you asked for that does not exist is an error; nothing found is just a note, and captions
    are still written.
    """
    from . import trend_research
    from . import trends as trends_mod

    env = os.environ if env is None else env
    explicit = cli_value or env.get("CLIPPER_TRENDS")
    if explicit and not Path(explicit).is_file():
        raise FileNotFoundError(f"trends file not found: {explicit}")

    loaded = trends_mod.load_trends(explicit)
    if trends_mod.is_unedited_template(loaded):
        _log(f"{loaded.path} is still the unedited template, so it is being ignored.")
        loaded = None
    if loaded:
        _log(trends_mod.describe(loaded, explicit))
        return loaded

    # Research runs if it is switched on, or if you asked for it right now with --refresh-trends;
    # --no-auto-trends beats both.
    if auto and (refresh or auto_trends_enabled(env)):
        ai = search_ai_config(env)
        if ai:
            found = trend_research.auto_trends(
                ai,
                niche=niche,
                region=resolve_region(region, env),
                force=refresh,
                log=_log,
                api=search_api(env),
            )
            _log(trends_mod.describe(found))
            return found
        _log(trends_mod.describe(None, explicit))  # wanted, but there is no key to do it with
        return None
    return None  # research is off and there is no list: nothing to report on every run


def _generate_social(
    session_dir: str | Path,
    session: Mapping[str, Any],
    chosen: list[dict[str, Any]],
    *,
    ai: Mapping[str, Any] | None = None,
    force: bool = False,
    niche: str | None = None,
    trends: Any = None,
) -> int:
    """Write a title, caption and hashtags for each chosen clip. Returns how many were written.

    A failure here never fails the run: the clips are already rendered by this point.
    """
    from . import social as social_mod

    if ai is None:
        try:
            ai = config.load_ai_config().as_dict()
        except config.ConfigError:
            _log("Skipping titles and captions: no AI key configured (set CLIPPER_API_KEY).")
            return 0

    language = session.get("output_language_name") or "the same language as the transcript"
    written = 0
    for h in chosen:
        index = h["_highlight_index"]
        if not force and social_mod.load_social(session_dir, index):
            continue
        try:
            copy = social_mod.generate_social_copy(h, ai, language=language, niche=niche, trends=trends)
        except Exception as exc:  # noqa: BLE001
            _log(f"Could not write a caption for clip {index + 1}: {str(exc)[:200]}")
            continue
        social_mod.save_social(session_dir, index, copy)
        written += 1
        _log(f"Clip {index + 1}: {copy['title']}\n   {social_mod.caption_line(copy)}")
        for variant in copy.get("variants", [])[1:]:
            _log(f"   alt ({variant['style']}): {variant['caption']}")
        if copy.get("pinned_comment"):
            _log(f"   pinned comment: {copy['pinned_comment']}")
        if copy.get("trend_tags_used"):
            _log(f"   uses trending tag(s): {', '.join('#' + t for t in copy['trend_tags_used'])}")
    return written


def _export(session_dir: str | Path, dest: str | None, open_after: bool) -> None:
    from . import export as export_mod

    result = export_mod.export_clips(session_dir, dest)
    print(f"\nExported {len(result.clips)} clip(s) to: {result.dest}")
    print("   captions.txt has a ready-to-paste caption for each; review.csv is the review sheet.")
    if sys.platform == "darwin":
        print("   To AirDrop: select the clips in Finder, then Share > AirDrop.")
    if open_after and not export_mod.reveal(result.dest):
        print(f"   (could not open the folder automatically; it is at {result.dest})")


def _cmd_find(args: argparse.Namespace) -> int:
    session = _find(args)
    print(f"\nSession: {session['session_dir']}")
    print(f"Next:    clipper process {session['session_dir']} --top {DEFAULT_TOP}")
    return 0


def _process_options(args: argparse.Namespace) -> dict[str, Any]:
    """The rendering and delivery options shared by `process` and `run`."""
    return dict(
        cookies=args.cookies,
        reframe=args.reframe,
        background=args.background,
        captions=not args.no_captions,
        hook=not args.no_hook,
        lead_in=args.lead_in,
        tail_out=args.tail_out,
        social=not args.no_social,
        export=args.export,
        export_to=args.export_to,
        open_after=args.open,
        niche=args.niche,
        trends_file=args.trends,
        region=args.region,
        auto_trends=not args.no_auto_trends,
        refresh_trends=args.refresh_trends,
    )


def _cmd_process(args: argparse.Namespace) -> int:
    _process(args.session_dir, pick=args.pick, top=args.top, **_process_options(args))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    session = _find(args)
    _process(session["session_dir"], pick=None, top=args.top, **_process_options(args))
    return 0


def _cmd_social(args: argparse.Namespace) -> int:
    session = load_session(args.session_dir)
    chosen = select_highlights(session, pick=args.pick, top=args.top)
    ai = config.load_ai_config().as_dict()
    niche = resolve_niche(args.niche)
    written = _generate_social(
        args.session_dir,
        session,
        chosen,
        ai=ai,
        force=args.force,
        niche=niche,
        trends=load_trends_for_run(
            args.trends,
            niche=niche,
            region=args.region,
            auto=not args.no_auto_trends,
            refresh=args.refresh_trends,
        ),
    )
    if written == 0:
        print("Nothing new to write (use --force to regenerate existing captions).")
    return 0


def _cmd_trends(args: argparse.Namespace) -> int:
    from . import trends as trends_mod

    path = args.file or os.environ.get("CLIPPER_TRENDS") or trends_mod.DEFAULT_TRENDS_FILE
    if args.init:
        created = trends_mod.init_template(path)
        print(f"Created {created}.")
        print("Open it, replace the examples with what is trending right now, and save.")
        print("Where to look: TikTok Creative Center > Trends")
        print("  https://ads.tiktok.com/help/article/how-to-use-trends")
        print("(You do not need this file: captions are written without trends too.)")
        return 0

    from . import trend_research

    niche = resolve_niche(args.niche)
    region = resolve_region(args.region)

    if args.diagnose:
        ai = search_ai_config()
        if ai is None:
            raise config.ConfigError("No API key configured. Set CLIPPER_API_KEY in .env to test web search.")
        for line in trend_research.diagnose(ai, niche=niche, region=region, api=search_api()):
            print(line)
        return 0

    manual = trends_mod.load_trends(path)
    if trends_mod.is_unedited_template(manual):
        manual = None
    if manual:
        print(trends_mod.describe(manual, path))
        print(f"Hashtags it will offer the model: {', '.join(f'#{t}' for t in manual.hashtags) or '(none)'}")
        print("(A trends file you wrote takes priority over automatic research.)")
        return 0

    cache_file = trend_research.cache_path(niche, region)
    if args.refresh:
        ai = search_ai_config()
        if ai is None:
            raise config.ConfigError("No API key configured. Set CLIPPER_API_KEY in .env to research trends.")
        trend_research.auto_trends(ai, niche=niche, region=region, force=True, log=_log, api=search_api())
    report = trend_research.load_cache(cache_file)
    if report is None:
        print(trends_mod.describe(None, path))
        return 1 if args.refresh else 0
    print(trends_mod.describe(trend_research.to_trends(report)))
    _print_report(report)
    return 0


def _print_report(report: Any) -> None:
    print(f"\nResearched with {report.model or 'the configured model'} for: "
          f"{report.niche or 'general entertainment'}, {report.region or 'worldwide'}")
    if report.hashtags:
        print("  hashtags: " + " ".join(f"#{t}" for t in report.hashtags))
    for label, items in (("formats", report.formats), ("topics", report.topics)):
        for item in items:
            print(f"  {label[:-1]}: {item}")
    if report.refused:
        print("  request forms refused first (a simpler one was used):")
        for entry in report.refused:
            print(f"    - {entry}")
    if report.n_sources:
        print(f"  sources returned by the search: {report.n_sources}; items kept: matched to them; "
              f"dropped as unverifiable: {report.dropped}")
        for url in report.source_urls[:5]:
            print(f"    - {url}")
    else:
        print("  The search returned NO sources, so nothing could be checked and these items are "
              "UNVERIFIED. If they look wrong, keep CLIPPER_AUTO_TRENDS at 0 (the default) and write "
              "your own list with `clipper trends --init`.")


def _cmd_review(args: argparse.Namespace) -> int:
    from . import review

    path = review.write_review_csv(args.session_dir, args.out)
    print(f"Review sheet: {path}")
    print("\nTo rate the picks in Google Sheets:")
    print("  1. Go to sheets.google.com and choose File > Import > Upload, then pick this file.")
    print("  2. Fill the my_rating column (1-10) for the clips you have judged.")
    print("  3. File > Download > Comma-separated values, then run:  clipper eval <that file>")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    _export(args.session_dir, args.to, args.open)
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    from . import evaluation

    rated = evaluation.load_rated(args.csv)
    print(evaluation.format_report(evaluation.evaluate(rated, good=args.good, k=args.k)))
    return 0


def _ffmpeg_install_hint(platform: str | None = None) -> str:
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return "Install it with: brew install ffmpeg"
    if platform.startswith("linux"):
        return "Install it with: sudo apt install ffmpeg (or your distro's package)"
    return "Install it from https://ffmpeg.org/download.html and put it on your PATH."


def _libass_hint(platform: str | None = None) -> str:
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return (
            "Some Homebrew builds lack libass: try `brew install ffmpeg-full` and put its bin "
            "directory first on your PATH."
        )
    return "Install an ffmpeg build with libass enabled (most distro packages have it)."


def _face_model_path() -> Path | None:
    """The MediaPipe face model if it is already on disk (bundled or cached), else None."""
    from .helpers import _find_bundled, _get_app_dir

    bundled = _find_bundled("models/face_landmarker.task")
    if bundled:
        return Path(bundled)
    cached = _get_app_dir() / "models" / "face_landmarker.task"
    return cached if cached.exists() else None


def _probe_face_landmarker(model_path: Path) -> None:
    """Create and close a FaceLandmarker; raises if MediaPipe's native libs can't load."""
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    options = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=1,
    )
    vision.FaceLandmarker.create_from_options(options).close()


def _cmd_doctor(args: argparse.Namespace) -> int:
    rows: list[tuple[str, str]] = []

    def ok(msg: str) -> None:
        rows.append(("ok", msg))

    def warn(msg: str) -> None:
        rows.append(("WARN", msg))

    def fail(msg: str) -> None:
        rows.append(("FAIL", msg))

    if sys.version_info >= (3, 11):
        ok(f"Python {sys.version.split()[0]}")
    else:
        fail(f"Python {sys.version.split()[0]}: 3.11+ required")

    from .helpers import get_deno_path, get_ffmpeg_path

    ffmpeg = get_ffmpeg_path()
    try:
        version = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=15)
        ok((version.stdout.splitlines() or ["ffmpeg"])[0])
        filters = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=15
        ).stdout
        if " ass " in filters or " subtitles " in filters:
            ok("ffmpeg has libass (captions can be burned in)")
        else:
            fail(f"ffmpeg has no libass, so captions cannot be burned in. {_libass_hint()}")
    except (OSError, subprocess.SubprocessError) as exc:
        fail(f"ffmpeg not runnable ({ffmpeg}): {exc}. {_ffmpeg_install_hint()}")

    for module, purpose in (
        ("yt_dlp", "downloads"),
        ("cv2", "video frames"),
        ("numpy", "arrays"),
        ("PIL", "hook text"),
        ("openai", "LLM client"),
    ):
        try:
            mod = importlib.import_module(module)
            if module == "yt_dlp":
                ok(f"yt-dlp {mod.version.__version__}")
            else:
                ok(f"{module} imports ({purpose})")
        except Exception as exc:  # noqa: BLE001 - report any import-time failure
            fail(f"{module} failed to import ({purpose}): {exc}")

    if _CA_FALLBACK:
        ok(f"TLS: this Python has no CA bundle, so CLIPPER is using certifi's ({_CA_FALLBACK})")
    else:
        paths = ssl.get_default_verify_paths()
        if paths.cafile or paths.capath or os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
            ok("TLS: CA certificates available")
        else:
            warn("TLS: no CA certificates found and certifi is missing; HTTPS downloads will fail. "
                 "Run: pip install certifi")

    if get_deno_path():
        ok("deno found")
    else:
        warn("deno not found; upstream logs that some YouTube formats may be unavailable without it")

    model_path = _face_model_path()
    if not mediapipe_available():
        warn(
            "mediapipe not installed: face tracking is off and `--reframe auto` uses centered. "
            "To enable it: pip install 'clipper[face]' (Apple Silicon, or Python 3.9-3.12 on "
            "Intel Macs)"
        )
    elif model_path is None:
        warn(
            "MediaPipe face model not cached; `--reframe face` downloads it on first use "
            "from storage.googleapis.com (`--reframe centered` needs no model)"
        )
    else:
        try:
            # Importing mediapipe succeeds without its native libraries; only creating
            # the landmarker loads them, so this is the check that matters.
            _probe_face_landmarker(model_path)
            ok("face tracking runtime works (MediaPipe face model loads)")
        except Exception as exc:  # noqa: BLE001
            warn(
                f"face tracking unavailable: {str(exc)[:200]}. `--reframe centered` still works. "
                "On Debian/Ubuntu this is usually: apt install libegl1 libgles2"
            )

    cfg = None
    try:
        cfg = config.load_ai_config()
        ok(f"AI: model {cfg.model} @ {cfg.base_url} (key {cfg.masked_key})")
    except config.ConfigError as exc:
        warn(str(exc))

    if args.check_ai and cfg:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=30, max_retries=1)
            reply = client.chat.completions.create(
                model=cfg.model,
                messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                max_tokens=5,
            )
            ok(f"AI endpoint answered: {reply.choices[0].message.content!r}")
        except Exception as exc:  # noqa: BLE001
            fail(f"AI endpoint call failed: {str(exc)[:300]}")
    elif args.check_ai:
        warn("--check-ai skipped: no AI config")

    cookies = os.environ.get("CLIPPER_COOKIES") or "cookies.txt"
    if Path(cookies).is_file():
        from .cookies import validate_cookies

        valid, error = validate_cookies(cookies)
        (ok if valid else fail)(f"cookies: {cookies}" if valid else f"cookies: {error}")
    else:
        warn(f"cookies file not found ({cookies}); `find` and `process` need one")

    for level, msg in rows:
        print(f"  [{level:<4}] {msg}")
    return 1 if any(level == "FAIL" for level, _ in rows) else 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipper",
        description="Turn long videos into vertical 9:16 short-form clips.",
        epilog="Built on top of yt-short-clipper by Aji Prakoso (jipraks), MIT licensed: "
        "https://github.com/jipraks/yt-short-clipper",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_cookies(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--cookies",
            help="YouTube cookies.txt (default: $CLIPPER_COOKIES, then ./cookies.txt)",
        )

    def add_social_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--niche",
            help="what your account is about, e.g. 'reality TV drama' (default: $CLIPPER_NICHE); "
            "keeps hashtags and wording consistent",
        )
        sp.add_argument(
            "--trends",
            metavar="FILE",
            help="your own file of what is trending (default: $CLIPPER_TRENDS, then ./trends.txt). "
            "Without one, captions use general best practice unless you switch on the experimental "
            "web research (CLIPPER_AUTO_TRENDS=1); see `clipper trends`",
        )
        sp.add_argument(
            "--region",
            help="where your audience is, e.g. 'United States' or 'UK' (default: $CLIPPER_REGION, "
            "else worldwide English-language); used when researching trends",
        )
        sp.add_argument(
            "--no-auto-trends",
            action="store_true",
            help="do not research trends this run, even if CLIPPER_AUTO_TRENDS=1",
        )
        sp.add_argument(
            "--refresh-trends",
            action="store_true",
            help="research trends now (even though automatic research is off by default), "
            "instead of using the cached result",
        )

    def add_find_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("url")
        sp.add_argument("--clips", type=int, default=5, help="how many highlights to request (default 5)")
        sp.add_argument("--sub-lang", default="en", help="subtitle language to fetch (default en)")
        sp.add_argument(
            "--direction",
            help="free-text steer for the highlight model, e.g. 'skip the intro; use 12:30 - 13:40'",
        )
        sp.add_argument(
            "--out-language", default="auto", help="language for titles/hooks (default: follow the video)"
        )
        sp.add_argument(
            "--profile",
            choices=profiles.PROFILE_NAMES,
            default=profiles.DEFAULT_PROFILE,
            help="what to look for: viral = drama, arguments, scandal, wild/WTF moments, funny "
            "punchlines, roasts, spicy-but-appropriate, emotion (default); podcast = upstream's prompt",
        )
        sp.add_argument(
            "--prompt-file",
            help="custom highlight prompt (overrides --profile); must contain {transcript} and {num_clips}",
        )
        sp.add_argument(
            "--window-minutes",
            type=float,
            default=DEFAULT_WINDOW_SECONDS / 60,
            help="long videos are scored in windows of this length (default 30)",
        )
        sp.add_argument(
            "--overlap-minutes",
            type=float,
            default=DEFAULT_OVERLAP_SECONDS / 60,
            help="overlap between windows, so a moment on a boundary is not cut in half (default 2)",
        )
        sp.add_argument(
            "--no-rerank",
            action="store_true",
            help="skip the final call that re-scores all windows' picks on one shared scale",
        )
        sp.add_argument("--out", default=DEFAULT_OUT, help=f"output folder (default ./{DEFAULT_OUT})")
        add_cookies(sp)

    def add_process_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--reframe",
            choices=["auto", "face", "centered", "original"],
            default="auto",
            help="auto = face tracking if MediaPipe is installed, else centered (default)",
        )
        sp.add_argument(
            "--background",
            choices=["black", "blurred"],
            default="blurred",
            help="behind the video when --reframe centered: a blurred copy of it (default) or black bars",
        )
        sp.add_argument("--no-captions", action="store_true", help="skip word-by-word captions")
        sp.add_argument("--no-hook", action="store_true", help="skip the hook text overlay")
        sp.add_argument(
            "--no-social", action="store_true", help="skip the title, caption and hashtags for each clip"
        )
        add_social_args(sp)
        sp.add_argument(
            "--export",
            action="store_true",
            help="afterwards copy the clips into one folder (default ~/Desktop/clipper-exports/<session>)",
        )
        sp.add_argument("--export-to", metavar="DIR", help="like --export, into this folder")
        sp.add_argument("--open", action="store_true", help="after exporting, open the folder in Finder")
        sp.add_argument("--lead-in", type=float, help="seconds added before each clip (default 1.5)")
        sp.add_argument("--tail-out", type=float, help="seconds added after each clip (default 2.5)")

    doctor = sub.add_parser("doctor", help="check ffmpeg, libraries, and configuration")
    doctor.add_argument("--check-ai", action="store_true", help="make one tiny call to the AI endpoint")
    doctor.set_defaults(func=_cmd_doctor)

    find = sub.add_parser("find", help="download subtitles and ask the AI for highlights")
    add_find_args(find)
    find.set_defaults(func=_cmd_find)

    process = sub.add_parser("process", help="render clips from a session made by `find`")
    process.add_argument("session_dir")
    group = process.add_mutually_exclusive_group()
    group.add_argument("--pick", help="1-based clip numbers, e.g. 1,3-4 (default: all)")
    group.add_argument("--top", type=int, help="render only the N highest-scoring clips")
    add_cookies(process)
    add_process_args(process)
    process.set_defaults(func=_cmd_process)

    run = sub.add_parser("run", help="find highlights, then render the top ones")
    add_find_args(run)
    run.add_argument(
        "--top", type=int, default=DEFAULT_TOP, help=f"render the N best clips (default {DEFAULT_TOP})"
    )
    add_process_args(run)
    run.set_defaults(func=_cmd_run)

    social = sub.add_parser("social", help="write a title, caption and hashtags for each clip")
    social.add_argument("session_dir")
    social_group = social.add_mutually_exclusive_group()
    social_group.add_argument("--pick", help="1-based clip numbers, e.g. 1,3-4 (default: all)")
    social_group.add_argument("--top", type=int, help="only the N highest-scoring clips")
    social.add_argument("--force", action="store_true", help="regenerate captions that already exist")
    add_social_args(social)
    social.set_defaults(func=_cmd_social)

    trends = sub.add_parser(
        "trends", help="show or create the file of current trends that captions draw on"
    )
    trends.add_argument("--init", action="store_true", help="create a template for your own trends file")
    trends.add_argument(
        "--refresh", action="store_true", help="research trends now (web search) and show what it found and how well it checked out"
    )
    trends.add_argument(
        "--diagnose",
        action="store_true",
        help="test each web-search request form on its own and show exactly what came back "
        "(a few small AI calls; writes nothing)",
    )
    trends.add_argument("--niche", help="what your account is about (default: $CLIPPER_NICHE)")
    trends.add_argument("--region", help="where your audience is (default: $CLIPPER_REGION)")
    trends.add_argument("--file", metavar="FILE", help="your own file (default: $CLIPPER_TRENDS, then ./trends.txt)")
    trends.set_defaults(func=_cmd_trends)

    review = sub.add_parser("review", help="write a review sheet (CSV) to rate the picks")
    review.add_argument("session_dir")
    review.add_argument("--out", metavar="FILE", help="where to write it (default: <session>/review.csv)")
    review.set_defaults(func=_cmd_review)

    export = sub.add_parser("export", help="gather finished clips into one folder, ready to AirDrop")
    export.add_argument("session_dir")
    export.add_argument("--to", metavar="DIR", help="destination (default ~/Desktop/clipper-exports/<session>)")
    export.add_argument("--open", action="store_true", help="open the folder in Finder afterwards")
    export.set_defaults(func=_cmd_export)

    evaluate = sub.add_parser(
        "eval", help="compare the model's virality scores with your own ratings from a review sheet"
    )
    evaluate.add_argument("csv", help="a review sheet with the my_rating column filled in")
    evaluate.add_argument("--good", type=float, default=7.0, help="your rating that counts as a good clip (default 7)")
    evaluate.add_argument("--k", type=int, default=3, help="check the model's top K picks (default 3)")
    evaluate.set_defaults(func=_cmd_eval)

    return parser


def main(argv: list[str] | None = None) -> int:
    global _CA_FALLBACK
    args = build_parser().parse_args(argv)
    config.load_dotenv_if_present()
    _CA_FALLBACK = ensure_ca_certificates()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except (config.ConfigError, ValueError, FileNotFoundError, FileExistsError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
