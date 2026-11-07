#!/bin/bash
# Mac: double-click this file in Finder to start planespotter.
# The first time, it walks you through setup. After that it starts watching.

cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 isn't installed yet. Install it from https://www.python.org/downloads/"
  echo "then double-click this file again."
  read -r -p "Press Enter to close."
  exit 1
fi

if [ ! -f .env ]; then
  python3 -m planespotter setup || { read -r -p "Press Enter to close."; exit 1; }
fi

echo
echo "Watching the sky. Leave this window open (you can minimise it)."
echo "Your Mac won't fall asleep while this runs. Close the window to stop."
echo
# caffeinate stops the Mac from idling to sleep while planespotter runs.
caffeinate -i python3 -m planespotter watch
read -r -p "planespotter stopped. Press Enter to close."
