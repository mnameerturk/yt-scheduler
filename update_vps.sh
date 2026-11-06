#!/usr/bin/env bash
# Update NMR Scheduler on the VPS to the latest code. Keeps .env, data/ and client_secret.json.
#   curl -fsSL https://raw.githubusercontent.com/mnameerturk/yt-scheduler/main/update_vps.sh | bash
set -euo pipefail
DIR="${DIR:-$HOME/yt-scheduler}"
TMP="$(mktemp -d)"
curl -fsSL https://codeload.github.com/mnameerturk/yt-scheduler/tar.gz/main | tar -xz -C "$TMP"
SRC="$TMP/yt-scheduler-main"
cp -r "$SRC/app/." "$DIR/app/"
cp "$SRC"/requirements.txt "$SRC"/*.sh "$DIR/"
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
rm -rf "$TMP"
sudo systemctl restart nmr-scheduler
sleep 3
echo "Scheduler: $(systemctl is-active nmr-scheduler)"
if [ -x "$HOME/.local/bin/claude" ]; then echo "Claude Code: installed"; else echo "Claude Code: not installed yet"; fi
