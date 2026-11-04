#!/usr/bin/env bash
# CLIPPER local setup: virtualenv + dependency checks.
# Usage: bash setup.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

ok()   { printf '  [ok]   %s\n' "$1"; }
warn() { printf '  [WARN] %s\n' "$1"; }
fail() { printf '  [FAIL] %s\n' "$1"; FAILED=1; }
FAILED=0

echo "Python"
command -v python3 >/dev/null || { echo "python3 not found (need 3.8+)"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  && ok "$(python3 --version)" || fail "Python 3.8+ required"

echo "Virtualenv + packages"
[ -d .venv ] || python3 -m venv .venv
# Upgrade setuptools first: pysrt's sdist build breaks on some distro setuptools.
./.venv/bin/python -m pip install --quiet --upgrade pip setuptools wheel
./.venv/bin/python -m pip install --quiet -r requirements.txt
./.venv/bin/python -I -c 'import yt_dlp, pysrt, dotenv' \
  && ok "yt-dlp, pysrt, python-dotenv import cleanly" || fail "package import failed"

echo "FFmpeg"
if command -v ffmpeg >/dev/null; then
  ok "$(ffmpeg -version | head -n1)"
  # Capture first: `grep -q` exits early, and under pipefail the resulting SIGPIPE
  # on ffmpeg would make a successful match look like a failure.
  FILTERS="$(ffmpeg -hide_banner -filters 2>&1 || true)"
  if grep -qE ' subtitles ' <<<"$FILTERS"; then
    ok "subtitles filter (libass) available: burned-in captions will work"
  else
    fail "ffmpeg has no libass. macOS: brew install ffmpeg-full | Ubuntu: apt install ffmpeg"
  fi
else
  fail "ffmpeg not found. macOS: brew install ffmpeg-full | Ubuntu: apt install ffmpeg"
fi

echo "Config"
if [ -f .env ]; then ok ".env already exists"; else cp .env.example .env && ok "created .env from .env.example"; fi

echo
if [ "$FAILED" -eq 0 ]; then
  echo "Ready. Activate with: source .venv/bin/activate"
else
  echo "Fix the [FAIL] items above, then re-run: bash setup.sh"; exit 1
fi
