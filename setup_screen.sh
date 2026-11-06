#!/usr/bin/env bash
# NMR Scheduler: Chrome + remote screen on the VPS (for Google Flow thumbnails).
# Screen is viewable from any browser at https://screen.<domain>/  (password protected)
#   bash setup_screen.sh
set -euo pipefail

DOMAIN="${DOMAIN:-nmrscheduler.duckdns.org}"
SCREEN_DOMAIN="screen.$DOMAIN"
U="$(whoami)"
H="$HOME"

echo "== Remote screen + Chrome setup =="
read -rsp "Choose a password for the remote screen (8+ characters): " PW; echo
[ "${#PW}" -ge 8 ] || { echo "Password too short."; exit 1; }

echo "-- Installing packages (a few minutes)"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xvfb fluxbox x11vnc novnc python3-websockify \
  fonts-noto fonts-noto-color-emoji wget dnsutils >/dev/null
if ! command -v google-chrome >/dev/null; then
  wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /tmp/chrome.deb >/dev/null
  rm -f /tmp/chrome.deb
fi

echo "-- Screen password"
mkdir -p "$H/.vnc" "$H/flow-profile" "$H/flow-ext"
x11vnc -storepasswd "$PW" "$H/.vnc/passwd" >/dev/null 2>&1
chmod 600 "$H/.vnc/passwd"
HASH="$(caddy hash-password --plaintext "$PW")"

unit() {  # name, description, after, command
  sudo tee "/etc/systemd/system/$1.service" >/dev/null <<EOF
[Unit]
Description=$2
After=$3
Requires=$3

[Service]
User=$U
Environment=DISPLAY=:1
Environment=HOME=$H
ExecStart=$4
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
}

echo "-- Background services"
sudo tee /etc/systemd/system/nmr-display.service >/dev/null <<EOF
[Unit]
Description=Virtual display :1
[Service]
User=$U
ExecStart=/usr/bin/Xvfb :1 -screen 0 1600x900x24 -nolisten tcp
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
unit nmr-wm     "Window manager"      nmr-display.service "/usr/bin/fluxbox"
unit nmr-vnc    "VNC server (local)"  nmr-display.service "/usr/bin/x11vnc -display :1 -localhost -rfbauth $H/.vnc/passwd -forever -shared -noxdamage -quiet"
unit nmr-novnc  "Browser screen"      nmr-vnc.service     "/usr/bin/websockify --web /usr/share/novnc 127.0.0.1:6080 localhost:5900"
unit nmr-chrome "Chrome for Flow"     nmr-display.service "/usr/bin/google-chrome --user-data-dir=$H/flow-profile --no-first-run --no-default-browser-check --disable-dev-shm-usage --window-position=0,0 --window-size=1600,900 https://labs.google/fx/tools/flow"

sudo systemctl daemon-reload
for s in nmr-display nmr-wm nmr-vnc nmr-novnc nmr-chrome; do
  sudo systemctl enable "$s" >/dev/null 2>&1
  sudo systemctl restart "$s"
  sleep 1
done

echo "-- HTTPS for the screen"
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}

$SCREEN_DOMAIN {
    basicauth {
        nmr $HASH
    }
    reverse_proxy 127.0.0.1:6080
}
EOF
sudo systemctl restart caddy

echo "-- Checks"
MYIP="$(curl -4 -s https://api.ipify.org || true)"
DNSIP="$(dig +short "$SCREEN_DOMAIN" A | tail -n1 || true)"
echo "$SCREEN_DOMAIN points to: ${DNSIP:-nothing}   (server: $MYIP)"
sleep 4
for s in nmr-display nmr-vnc nmr-novnc nmr-chrome; do
  printf '%-12s %s\n' "$s" "$(systemctl is-active $s)"
done
free -h | sed -n '1,2p'
echo
echo "Done. On your phone or PC open:"
echo "  https://$SCREEN_DOMAIN/vnc.html?autoconnect=1&resize=scale"
echo "Login: username nmr + the password you just chose (asked twice: browser, then screen)."
