#!/usr/bin/env bash
# Convenience wrapper for the systemd/Quadlet deployment (see setup-systemd.sh):
# rebuilds the image with the current code, then restarts bugbot.service so it
# picks it up. Safe to re-run any time after pulling code or config changes.
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

# bugbot.db is bind-mounted (see bugbot.container). If it's missing - e.g. it was
# deleted, or this is a fresh checkout - Podman would create an empty directory
# there instead of a file, which breaks sqlite3.connect() inside the container.
touch bugbot.db

echo "🏗️  Building updated image..."
podman build -t bugbot .

# Die Unit gleich mit erneuern statt nur neu zu starten: in bugbot.container steht für
# jede JSON-Konfiguration ein eigener Bind-Mount mit vollem Pfad. Zieht eine davon um,
# zeigt die *installierte* Unit weiter auf die alte Datei, und der Container startet gar
# nicht mehr ("statfs ...: no such file or directory") - obwohl das Repo längst stimmt.
# Das hier ist derselbe Schritt wie in setup-systemd.sh und genauso wiederholbar.
echo "📄 Refreshing Quadlet unit..."
sed "s#/path/to/bugbot#$(pwd)#g" bugbot.container > "$HOME/.config/containers/systemd/bugbot.container"
systemctl --user daemon-reload

echo "🔄 Restarting bugbot.service..."
systemctl --user restart bugbot.service

echo "✅ bugbot updated and running. Logs: journalctl --user-unit=bugbot.service -f"
