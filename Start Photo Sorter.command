#!/bin/bash
# Double-click to start Photo Sorter (macOS). Close this window or press Ctrl+C to stop.
cd "$(dirname "$0")" || exit 1

# If this folder was installed with git (GitHub), fetch the latest version first.
# This only downloads Photo Sorter's own code; no photo or data is sent.
if [ -d .git ] && /usr/bin/xcrun --find git >/dev/null 2>&1; then
  echo "Checking for a newer version of Photo Sorter..."
  git pull --ff-only --quiet 2>/dev/null && echo "Up to date." || echo "Could not update; starting the version on this computer."
fi

PY=""
for p in /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/bin/python3; do
  if [ -x "$p" ] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 or newer was not found. Install Python from https://www.python.org/downloads/macos/"
  read -r -p "Press Enter to close."
  exit 1
fi
"$PY" -m photosorter "$@"
read -r -p "Press Enter to close this window."
