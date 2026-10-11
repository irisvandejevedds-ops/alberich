#!/usr/bin/env bash
# Copy the app to /opt/finapp/app (root-owned, world-readable) and restart the services.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)/app"
sudo rm -rf /opt/finapp/app.new && sudo mkdir -p /opt/finapp/app.new
sudo cp -r "$SRC/finapp" /opt/finapp/app.new/finapp
sudo find /opt/finapp/app.new -name __pycache__ -type d -prune -exec rm -rf {} +
sudo chmod -R a+rX /opt/finapp/app.new
sudo rm -rf /opt/finapp/app.old
[ -d /opt/finapp/app ] && sudo mv /opt/finapp/app /opt/finapp/app.old
sudo mv /opt/finapp/app.new /opt/finapp/app
sudo systemctl restart finapp-web finapp-worker
echo "deployed; services restarted"
