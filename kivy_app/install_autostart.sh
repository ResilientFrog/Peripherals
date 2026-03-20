#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="kivy_rtk.service"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
ENV_FILE="/etc/default/kivy_rtk"

if [[ "${EUID}" -ne 0 ]]; then
  echo "❌ Run with sudo: sudo bash install_autostart.sh"
  exit 1
fi

APP_USER="${SUDO_USER:-berries}"
APP_HOME="$(getent passwd "${APP_USER}" | cut -d: -f6)"
if [[ -z "${APP_HOME}" ]]; then
  echo "❌ Could not resolve home directory for user '${APP_USER}'"
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SCRIPT="${APP_DIR}/run_kivy.sh"

if [[ ! -f "${RUN_SCRIPT}" ]]; then
  echo "❌ Missing launcher: ${RUN_SCRIPT}"
  exit 1
fi

chmod +x "${RUN_SCRIPT}"

cat > "${ENV_FILE}" <<'EOF'
# Kivy RTK service environment
# Update these values if your base AP or RTCM endpoint changes.

BASE_WIFI_SSID=CHANGE_ME_SSID
BASE_WIFI_PASSWORD=CHANGE_ME_PASSWORD
BASE_WIFI_IFACE=wlan0

# Optional explicit RTCM endpoint. Leave as defaults for auto-host discovery.
RTCM_BASE_HOST=192.168.4.1
RTCM_BASE_PORT=2101
# RTCM_BASE_HOSTS=192.168.4.1,192.168.1.1
EOF
chmod 600 "${ENV_FILE}"

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Kivy RTK Rover App
After=network-online.target graphical.target NetworkManager.service
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=DISPLAY=:0
Environment=XAUTHORITY=${APP_HOME}/.Xauthority
EnvironmentFile=-${ENV_FILE}
ExecStart=/bin/bash ${RUN_SCRIPT}
Restart=always
RestartSec=3

[Install]
WantedBy=graphical.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}" || true

echo "✅ Installed ${SERVICE_NAME}"
echo "ℹ️  Check status: sudo systemctl status ${SERVICE_NAME}"
echo "ℹ️  View logs:    sudo journalctl -u ${SERVICE_NAME} -f"
echo "ℹ️  Edit config:  sudo nano ${ENV_FILE}"
