#!/usr/bin/env bash
# One-time setup of the Podman Quadlet unit (bugbot.container) so systemd manages
# the container directly - "systemctl restart bugbot" instead of manual podman
# run/stop/rm. Safe to re-run: it just re-installs the unit and reloads/restarts.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
REPO_DIR="$(pwd)"
QUADLET_DIR="$HOME/.config/containers/systemd"

if [[ ! -f .env ]]; then
    echo "⚠️  .env not found. Copy .env.example to .env and fill in your credentials first." >&2
    exit 1
fi

# bugbot.db is bind-mounted (see bugbot.container). If the source file doesn't exist
# yet, Podman creates an empty directory there instead of a file, which breaks
# sqlite3.connect() inside the container - so make sure it exists first.
touch bugbot.db

echo "🏗️  Building image..."
podman build -t bugbot .

echo "📄 Installing Quadlet unit to $QUADLET_DIR..."
mkdir -p "$QUADLET_DIR"
sed "s#/path/to/bugbot#$REPO_DIR#g" bugbot.container > "$QUADLET_DIR/bugbot.container"

echo "🔄 Reloading systemd user units..."
systemctl --user daemon-reload

echo "🔌 Enabling lingering so the service survives logout/reboot..."
loginctl enable-linger "$USER"

echo "🚀 Starting bugbot.service..."
systemctl --user start bugbot.service

echo "✅ Done."
echo "   Status: systemctl --user status bugbot"
echo "   Logs:   journalctl --user-unit=bugbot.service -f"
echo "   Update: rebuild the image (podman build -t bugbot .), then systemctl --user restart bugbot"
