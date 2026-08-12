#!/usr/bin/env bash
# Reverses setup-tunnel.sh: stops the tunnel, removes its systemd user service and
# takes the ~/.ssh/config entry back out. Runs on the OBS machine, like its counterpart.
#
# Only the block between our own markers is removed - a Host entry someone wrote by hand
# is left alone, because this script has no way to know what else it is used for. User
# lingering stays enabled too: setup-systemd.sh may rely on it on the same machine.
set -euo pipefail

ALIAS="${BUGBOT_TUNNEL_ALIAS:-bugbot}"
SSH_CONFIG="$HOME/.ssh/config"
UNIT="$HOME/.config/systemd/user/bugbot-tunnel.service"
BEGIN="# >>> bugbot tunnel >>>"
END="# <<< bugbot tunnel <<<"

if [[ ! -f "$UNIT" ]] && ! grep -qF "$BEGIN" "$SSH_CONFIG" 2>/dev/null; then
    echo "ℹ️  Nothing installed by setup-tunnel.sh - nothing to remove."
    exit 0
fi

if [[ -f "$UNIT" ]]; then
    echo "🛑 Stopping bugbot-tunnel.service..."
    systemctl --user disable --now bugbot-tunnel.service 2>/dev/null || true

    echo "🗑️  Removing $UNIT..."
    rm -f "$UNIT"

    echo "🔄 Reloading systemd user units..."
    systemctl --user daemon-reload
fi

if grep -qF "$BEGIN" "$SSH_CONFIG" 2>/dev/null; then
    echo "🗑️  Removing the '$ALIAS' entry from $SSH_CONFIG..."
    sed -i "/^$BEGIN$/,/^$END$/d" "$SSH_CONFIG"
fi

echo "✅ Tunnel removed - the ports are no longer forwarded."
echo "   (Lingering and any hand-written ssh config were left untouched."
echo "    Run ./setup-tunnel.sh user@server to set it up again.)"
