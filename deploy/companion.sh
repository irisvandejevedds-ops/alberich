#!/usr/bin/env bash
# One-time setup of the phone companion: a self-signed certificate (the phone pins it, so it needs no certificate authority) and a second service that
# answers ONLY the sync route over TLS on the home network. It gets its own small environment file, so it never carries the Claude key or other secrets.
# Safe to run again; it keeps an existing certificate.
set -euo pipefail
PORT="${FINAPP_SYNC_PORT:-8121}"
sudo install -d -m 700 -o finapp -g finapp /var/lib/finapp/tls
if [ ! -f /var/lib/finapp/tls/cert.pem ]; then
  sudo -u finapp openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=alberich-companion" \
    -keyout /var/lib/finapp/tls/key.pem -out /var/lib/finapp/tls/cert.pem 2>/dev/null
  sudo chmod 600 /var/lib/finapp/tls/key.pem
fi
sudo tee /etc/finapp/finapp-sync.env >/dev/null <<ENV
FINAPP_DATA_DIR=/var/lib/finapp
FINAPP_TZ=America/Toronto
FINAPP_SYNC_ONLY=1
FINAPP_SYNC_PORT=$PORT
FINAPP_REQUIRE_TS_HEADER=0
ENV
sudo chown root:finapp /etc/finapp/finapp-sync.env && sudo chmod 640 /etc/finapp/finapp-sync.env
sudo tee /etc/systemd/system/finapp-sync.service >/dev/null <<UNIT
[Unit]
Description=Alberich phone companion (sync route only, TLS)
After=network.target

[Service]
User=finapp
Group=finapp
EnvironmentFile=/etc/finapp/finapp-sync.env
Environment=PYTHONDONTWRITEBYTECODE=1
WorkingDirectory=/opt/finapp/app
ExecStart=/opt/finapp/venv/bin/uvicorn finapp.main:get_app --factory --host 0.0.0.0 --port $PORT --no-server-header --ssl-keyfile /var/lib/finapp/tls/key.pem --ssl-certfile /var/lib/finapp/tls/cert.pem
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/finapp
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
MemoryMax=200M

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now finapp-sync
sudo systemctl restart finapp-sync
echo "companion listening on port $PORT"
