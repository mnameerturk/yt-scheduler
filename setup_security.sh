#!/usr/bin/env bash
# NMR Scheduler VPS: security basics.
#  - fail2ban: blocks IPs that keep guessing the SSH password
#  - automatic security updates
#  - SSH: key login only (passwords switched off) -- ONLY if a key is already installed,
#    so you can't lock yourself out. The OVH web console (KVM) still works with the password.
#   curl -fsSL https://raw.githubusercontent.com/mnameerturk/yt-scheduler/main/setup_security.sh | bash
set -euo pipefail

echo "-- fail2ban + automatic security updates"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban unattended-upgrades >/dev/null
sudo tee /etc/fail2ban/jail.d/nmr.local >/dev/null <<'J'
[sshd]
enabled = true
maxretry = 5
findtime = 10m
bantime = 1h
J
sudo systemctl enable --now fail2ban >/dev/null 2>&1
sudo systemctl restart fail2ban
echo 'APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";' | sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null

echo "-- SSH"
if grep -qE '^(ssh-ed25519|ssh-rsa|ecdsa-)' "$HOME/.ssh/authorized_keys" 2>/dev/null; then
  sudo tee /etc/ssh/sshd_config.d/00-nmr-keys-only.conf >/dev/null <<'S'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
S
  sudo sshd -t
  sudo systemctl reload ssh
  echo "SSH: password login is OFF, key login only."
else
  echo "SSH: no key installed yet, so password login stays ON (nothing changed)."
fi

echo
echo "fail2ban: $(systemctl is-active fail2ban)   auto-updates: on"
sudo fail2ban-client status sshd | grep -E 'Currently banned|Total banned' || true
