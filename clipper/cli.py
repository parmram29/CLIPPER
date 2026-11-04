"""Headless command-line interface for CLIPPER.

    clipper doctor [--check-ai]
    clipper find URL [--clips N] [--direction TEXT] [--prompt-file FILE] ...
    clipper process SESSION_DIR [--pick 1,3-4 | --top N] [--reframe face|centered|original] ...
    clipper run URL [--clips N] [--top N] ...

Not part of the upstream yt-short-clipper code, which drives these same pipeline
functions from a desktop GUI over a JSON-lines sidecar protocol.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from . import config

DEFAULT_OUT = "out"
DEFAULT_TOP = 3
# Without {transcript} the model never sees the video; without {num_clips} the
# count it is asked for is undefined. Both are filled in by str.replace upstream.
REQUIRED_PROMPT_PLACEHOLDERS = ("{transcript}", "{num_clips}")


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


def _find(args: argparse.Namespace) -> dict[str, Any]:
    ai = config.load_ai_config().as_dict()
    if args.prompt_file:
        ai["system_message"] = load_prompt_file(args.prompt_file)
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
) -> dict[str, Any]:
    session = load_session(session_dir)
    chosen = select_highlights(session, pick=pick, top=top)
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
    return result


def _cmd_find(args: argparse.Namespace) -> int:
    session = _find(args)
    print(f"\nSession: {session['session_dir']}")
    print(f"Next:    clipper process {session['session_dir']} --top {DEFAULT_TOP}")
    return 0


def _cmd_process(args: argparse.Namespace) -> int:
    _process(
        args.session_dir,
        cookies=args.cookies,
        pick=args.pick,
        top=args.top,
        reframe=args.reframe,
        background=args.background,
        captions=not args.no_captions,
        hook=not args.no_hook,
        lead_in=args.lead_in,
        tail_out=args.tail_out,
    )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    session = _find(args)
    _process(
        session["session_dir"],
        cookies=args.cookies,
        pick=None,
        top=args.top,
        reframe=args.reframe,
        background=args.background,
        captions=not args.no_captions,
        hook=not args.no_hook,
        lead_in=args.lead_in,
        tail_out=args.tail_out,
    )
    return 0


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

    if sys.version_info >= (3, 10):
        ok(f"Python {sys.version.split()[0]}")
    else:
        fail(f"Python {sys.version.split()[0]}: 3.10+ required")

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
            fail("ffmpeg has no libass; captions cannot be burned in")
    except (OSError, subprocess.SubprocessError) as exc:
        fail(f"ffmpeg not runnable ({ffmpeg}): {exc}")

    for module, purpose in (
        ("yt_dlp", "downloads"),
        ("cv2", "video frames"),
        ("numpy", "arrays"),
        ("PIL", "hook text"),
        ("openai", "LLM client"),
        ("mediapipe", "face tracking"),
    ):
        try:
            mod = importlib.import_module(module)
            if module == "yt_dlp":
                ok(f"yt-dlp {mod.version.__version__}")
            else:
                ok(f"{module} imports ({purpose})")
        except Exception as exc:  # noqa: BLE001 - report any import-time failure
            fail(f"{module} failed to import ({purpose}): {exc}")

    if get_deno_path():
        ok("deno found")
    else:
        warn("deno not found; upstream logs that some YouTube formats may be unavailable without it")

    model_path = _face_model_path()
    if model_path is None:
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
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_cookies(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--cookies",
            help="YouTube cookies.txt (default: $CLIPPER_COOKIES, then ./cookies.txt)",
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
            "--prompt-file",
            help="custom highlight prompt; must contain {transcript} and {num_clips}",
        )
        sp.add_argument("--out", default=DEFAULT_OUT, help=f"output folder (default ./{DEFAULT_OUT})")
        add_cookies(sp)

    def add_process_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--reframe", choices=["face", "centered", "original"], default="face")
        sp.add_argument(
            "--background",
            choices=["black", "blurred"],
            default="black",
            help="bars for --reframe centered (default black)",
        )
        sp.add_argument("--no-captions", action="store_true", help="skip word-by-word captions")
        sp.add_argument("--no-hook", action="store_true", help="skip the hook text overlay")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config.load_dotenv_if_present()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except (config.ConfigError, ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
