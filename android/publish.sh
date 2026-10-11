#!/usr/bin/env bash
# Build the widget APK and hand it to the web app so it can be downloaded from /settings/widget.
set -euo pipefail
cd "$(dirname "$0")"
./build.sh >/dev/null
sudo install -d -o finapp -g finapp -m 0750 /var/lib/finapp/apk
sudo install -o finapp -g finapp -m 0640 build/alberich-widget.apk /var/lib/finapp/apk/alberich-widget.apk
echo "published $(stat -c %s build/alberich-widget.apk) bytes"
