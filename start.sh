#!/usr/bin/env bash
# Convenience wrapper around "systemctl --user start bugbot" for the Podman Quadlet
# deployment (see setup-systemd.sh) - starts the bot if it isn't already running.
# Doesn't rebuild the image; use update.sh after pulling code or config changes.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ ! -f .env ]]; then
    echo "⚠️  .env not found. Copy .env.example to .env and fill in your credentials first." >&2
    exit 1
fi

if [[ ! -f "$HOME/.config/containers/systemd/bugbot.container" ]]; then
    echo "⚠️  systemd unit not installed yet. Run ./setup-systemd.sh once first." >&2
    exit 1
fi

echo "🚀 Starting bugbot.service..."
systemctl --user start bugbot.service

echo "✅ bugbot is running. Logs: journalctl --user-unit=bugbot.service -f"
