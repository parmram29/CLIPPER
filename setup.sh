#!/usr/bin/env bash
# CLIPPER local setup: virtualenv, editable install, config file, health check.
# Usage: bash setup.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

command -v python3 >/dev/null || { echo "python3 not found (need 3.10+)"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10+ required, found $(python3 --version)"; exit 1; }

echo "Python: $(python3 --version) on $(python3 -c 'import platform; print(platform.system(), platform.machine())')"

echo "Creating virtualenv and installing CLIPPER..."
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python -m pip install --quiet --upgrade pip setuptools wheel
./.venv/bin/python -m pip install --quiet -e ".[dev]"

# Face tracking is optional: MediaPipe has no build for every Python/CPU (for example,
# Intel Macs on Python 3.13+). Without it CLIPPER still works with --reframe centered.
echo "Installing optional face tracking (MediaPipe)..."
if ./.venv/bin/python -m pip install --quiet -e ".[face]" 2>/dev/null; then
  echo "  face tracking installed"
else
  echo "  MediaPipe is not available for this Python/CPU, so face tracking is off."
  echo "  Everything else works; clips use --reframe centered."
  echo "  (Intel Mac? MediaPipe supports Python 3.9-3.12 there, not 3.13+.)"
fi

if [ -f .env ]; then
  echo ".env already exists, leaving it alone"
else
  cp .env.example .env
  echo "Created .env from .env.example: add your CLIPPER_API_KEY"
fi

echo
echo "Health check:"
./.venv/bin/clipper doctor || { echo; echo "Fix the [FAIL] items above, then re-run: bash setup.sh"; exit 1; }

echo
echo "Ready. Activate with:  source .venv/bin/activate"
echo "Then:                  clipper --help"
