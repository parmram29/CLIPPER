#!/bin/bash
# Double-click this file to open CLIPPER (the point-and-click app).
# It sets itself up the first time, which takes a few minutes and needs internet.
# Linux: run `bash CLIPPER.command` instead.

pause() {
  # Keep the window open, so any message can be read before it closes.
  read -r -p "Press Return to close this window. " _
}

# Which folder is CLIPPER? Normally the one this file sits in, wherever that folder was moved to. A shortcut (a symbolic
# link) to this file is followed to the real file. A COPY of this file somewhere else (the Desktop, say) cannot find the
# app next to itself, so it looks in the usual places, and if it still cannot, it says what to do instead of failing with
# a puzzling error from the setup step.
looks_like_clipper() {
  [ -f "$1/setup.sh" ] && [ -f "$1/pyproject.toml" ] && [ -f "$1/clipper/__init__.py" ]
}

SOURCE="${BASH_SOURCE[0]:-$0}"
while [ -L "$SOURCE" ]; do
  LINK_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  case "$SOURCE" in /*) ;; *) SOURCE="$LINK_DIR/$SOURCE" ;; esac
done
HERE="$(cd -P "$(dirname "$SOURCE")" && pwd)"

if ! looks_like_clipper "$HERE"; then
  COPY_DIR="$HERE"
  for candidate in "${CLIPPER_HOME:-}" "$HOME/CLIPPER" "$HOME/Documents/CLIPPER" "$HOME/Desktop/CLIPPER" "$HOME/Downloads/CLIPPER"; do
    if [ -n "$candidate" ] && looks_like_clipper "$candidate"; then
      HERE="$candidate"
      break
    fi
  done
  if ! looks_like_clipper "$HERE"; then
    echo "CLIPPER"
    echo "-------"
    echo "This copy of CLIPPER.command is not inside the CLIPPER folder, so it cannot find the app."
    echo "Open the CLIPPER folder and double-click the CLIPPER.command that is in there."
    echo "For a shortcut on the Desktop: right-click that file, choose Make Alias, then move the alias to the Desktop."
    pause
    exit 1
  fi
  echo "(This file is a copy in $COPY_DIR. Using the CLIPPER folder at $HERE.)"
fi
cd "$HERE" || exit 1

PY=".venv/bin/python"

ready() {
  # The environment exists and everything the app needs can be imported from this folder.
  [ -x "$PY" ] && "$PY" -c "import yt_dlp, anthropic, openai, cv2, numpy, PIL, dotenv, certifi, clipper.gui.server" >/dev/null 2>&1
}

echo "CLIPPER"
echo "-------"

if ! ready; then
  echo "Getting CLIPPER ready (first time, or the folder was moved). A few minutes, needs internet."
  if ! command -v python3 >/dev/null 2>&1; then
    echo
    echo "Python 3 isn't installed. Install it from https://www.python.org/downloads/ and double-click this file again."
    pause
    exit 1
  fi
  # setup.sh ends with a health check that can fail only because the AI key isn't filled in yet.
  # That is fine here: the app's Setup page walks through it. What matters is whether it is ready.
  bash setup.sh
  if ! ready; then
    echo
    echo "Setup didn't finish (see the messages above). Fix that, then double-click this file again."
    pause
    exit 1
  fi
fi

echo
echo "Starting CLIPPER. Your browser will open in a moment."
echo "Keep this window open while you use CLIPPER. Close it, or press Ctrl+C, to stop."
echo
"$PY" -m clipper gui
status=$?
echo
echo "CLIPPER has stopped."
pause
exit "$status"
