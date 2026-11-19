#!/bin/bash
# Double-click this file to open CLIPPER (the point-and-click app).
# It sets itself up the first time, which takes a few minutes and needs internet.
# Linux: run `bash CLIPPER.command` instead.

cd "$(dirname "$0")" || exit 1

PY=".venv/bin/python"

pause() {
  # Keep the window open, so any message can be read before it closes.
  read -r -p "Press Return to close this window. " _
}

ready() {
  # The environment exists and everything the app needs can be imported from this folder.
  [ -x "$PY" ] && "$PY" -c "import yt_dlp, openai, cv2, numpy, PIL, dotenv, certifi, clipper.gui.server" >/dev/null 2>&1
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
