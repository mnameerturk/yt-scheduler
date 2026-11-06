#!/usr/bin/env bash
# NMR Scheduler: one-time setup on an Ubuntu 24.04 VPS.
# Run from the folder that holds app/, .env, client_secret.json and data/:
#   bash setup_vps.sh
set -euo pipefail

DOMAIN="${DOMAIN:-nmrscheduler.duckdns.org}"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_USER="$(whoami)"

echo "== NMR Scheduler VPS setup =="
echo "Folder: $APP_DIR   Domain: $DOMAIN   User: $APP_USER"

[ -f "$APP_DIR/client_secret.json" ] || { echo "client_secret.json is missing in $APP_DIR"; exit 1; }
[ -f "$APP_DIR/.env" ] || { echo ".env is missing in $APP_DIR"; exit 1; }

echo "-- Installing system packages (takes a few minutes)"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip caddy ufw curl dnsutils >/dev/null

echo "-- Adding 2 GB swap (safety net for Chrome/Flow later)"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "-- Python environment"
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "-- Pointing .env at https://$DOMAIN"
sed -i 's/\r$//' .env
if grep -q '^BASE_URL=' .env; then
  sed -i "s|^BASE_URL=.*|BASE_URL=https://$DOMAIN|" .env
else
  echo "BASE_URL=https://$DOMAIN" >> .env
fi
mkdir -p data/thumbs
chmod 600 .env client_secret.json

echo "-- Background service (starts on boot, restarts if it crashes)"
sudo tee /etc/systemd/system/nmr-scheduler.service >/dev/null <<EOF
[Unit]
Description=NMR Scheduler
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now nmr-scheduler >/dev/null 2>&1
sudo systemctl restart nmr-scheduler

echo "-- HTTPS (Caddy gets the certificate automatically)"
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
EOF
sudo systemctl enable caddy >/dev/null 2>&1
sudo systemctl restart caddy

echo "-- Firewall: only SSH, HTTP, HTTPS"
sudo ufw allow OpenSSH >/dev/null
sudo ufw allow 80/tcp >/dev/null
sudo ufw allow 443/tcp >/dev/null
sudo ufw --force enable >/dev/null

echo "-- Checks"
MYIP="$(curl -4 -s https://api.ipify.org || true)"
DNSIP="$(dig +short "$DOMAIN" A | tail -n1 || true)"
echo "Server IP: $MYIP   $DOMAIN points to: $DNSIP"
if [ -n "$MYIP" ] && [ "$MYIP" != "$DNSIP" ]; then
  echo "WARNING: DuckDNS does not point to this server yet. Update it at duckdns.org, then run: sudo systemctl restart caddy"
fi
sleep 3
if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/ | grep -qE '^(200|30[0-9]|401)$'; then
  echo "Scheduler: running"
else
  echo "Scheduler: NOT responding. Show the log with: sudo journalctl -u nmr-scheduler -n 50"
fi
echo
echo "Done. Open https://$DOMAIN in a browser (first load can take ~30 s while the certificate is issued)."
