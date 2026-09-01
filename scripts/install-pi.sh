#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip chromium-browser unclutter gpsd gpsd-clients || \
  sudo apt-get install -y python3-venv python3-pip chromium unclutter gpsd gpsd-clients

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

sudo tee /etc/systemd/system/bcu.service >/dev/null <<EOF
[Unit]
Description=Touchscreen BCU
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/run.py
Restart=always
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now bcu.service

mkdir -p "/home/${USER_NAME}/.config/autostart"
cat > "/home/${USER_NAME}/.config/autostart/bcu-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=BCU Kiosk
Exec=${APP_DIR}/scripts/kiosk.sh
X-GNOME-Autostart-enabled=true
EOF

chmod +x "$APP_DIR/scripts/kiosk.sh"

if command -v timedatectl >/dev/null; then
  sudo timedatectl set-ntp true || true
  sudo timedatectl set-timezone Australia/Adelaide || true
fi

echo "BCU installed. Reboot the Pi to open the kiosk."
echo "Private GitHub updates: put a repo token in ${APP_DIR}/data/github.token"
