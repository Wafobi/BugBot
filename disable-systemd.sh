#!/usr/bin/env bash
# Reverses setup-systemd.sh: stops bugbot.service and removes the installed Quadlet
# unit so it no longer starts on boot. Quadlet-generated units can't be disabled via
# "systemctl --user disable" (it errors "unit is transient or generated" - see commit
# 1807b02); removing the installed .container file and reloading systemd is what
# actually undoes the [Install] WantedBy=default.target auto-start. Doesn't touch the
# built image, bugbot.db, or user lingering - run ./setup-systemd.sh again to re-enable.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
QUADLET_FILE="$HOME/.config/containers/systemd/bugbot.container"

if [[ ! -f "$QUADLET_FILE" ]]; then
    echo "ℹ️  systemd unit not installed - nothing to disable."
    exit 0
fi

echo "🛑 Stopping bugbot.service..."
systemctl --user stop bugbot.service || true

echo "🗑️  Removing installed Quadlet unit..."
rm -f "$QUADLET_FILE"

echo "🔄 Reloading systemd user units..."
systemctl --user daemon-reload

echo "✅ bugbot.service disabled - it will no longer start on boot."
echo "   (Image, bugbot.db, and lingering were left untouched. Run ./setup-systemd.sh to re-enable.)"
