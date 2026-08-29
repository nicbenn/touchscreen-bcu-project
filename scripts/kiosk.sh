#!/usr/bin/env bash
set -euo pipefail
unclutter -idle 0.3 -root &
sleep 2
CHROME="chromium-browser"
command -v "$CHROME" >/dev/null 2>&1 || CHROME="chromium"
exec "$CHROME" \
  --kiosk \
  --app=http://127.0.0.1:8080 \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --check-for-update-interval=31536000 \
  --incognito
