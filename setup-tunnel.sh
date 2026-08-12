#!/usr/bin/env bash
# One-time setup of the SSH tunnel from the OBS machine to the bot.
#
# NOTE: this one runs on the *OBS machine*, not on the server - unlike every other
# script here. It belongs to the same reversed direction as platforms/obs/obs_bridge.py:
# the bot's listeners sit on the server's loopback and are deliberately not reachable
# from the internet, so the way to them is a tunnel dialled from this side.
#
#   4456  obs-websocket relay   (platforms/obs)
#   4457  overlay listener      (features/overlay)
#
# It writes an ~/.ssh/config entry carrying both forwards, installs a systemd user
# service so the tunnel survives a dropped connection and a reboot, and checks that the
# far end actually answers. Safe to re-run: the config block is replaced, not appended.
#
#   ./setup-tunnel.sh user@server
#   BUGBOT_TUNNEL_PORTS="4456 4457" ./setup-tunnel.sh user@server
set -euo pipefail

ALIAS="${BUGBOT_TUNNEL_ALIAS:-bugbot}"
PORTS="${BUGBOT_TUNNEL_PORTS:-4456 4457}"
SSH_CONFIG="$HOME/.ssh/config"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/bugbot-tunnel.service"
BEGIN="# >>> bugbot tunnel >>>"
END="# <<< bugbot tunnel <<<"

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
    read -rp "Server (user@host): " TARGET
fi
if [[ "$TARGET" != *@* ]]; then
    echo "⚠️  Expected user@host, got '$TARGET'." >&2
    exit 1
fi
USER_PART="${TARGET%@*}"
HOST_PART="${TARGET#*@}"

# A hand-written entry for the same alias would silently lose against ours, or ours
# against it, depending on order - ssh takes the first value it sees for each keyword.
# Rather than guess which one the operator meant, stop and let them decide.
if [[ -f "$SSH_CONFIG" ]] && grep -qiE "^[[:space:]]*Host[[:space:]]+.*\b$ALIAS\b" "$SSH_CONFIG" \
   && ! grep -qF "$BEGIN" "$SSH_CONFIG"; then
    echo "⚠️  ~/.ssh/config already has a 'Host $ALIAS' that this script did not write." >&2
    echo "    Remove it first, or set BUGBOT_TUNNEL_ALIAS to a different name." >&2
    exit 1
fi

echo "📄 Writing the '$ALIAS' entry to $SSH_CONFIG..."
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$SSH_CONFIG"
# Drop a previous block of ours before writing the new one, so re-running updates
# instead of stacking a second Host with the same name.
if grep -qF "$BEGIN" "$SSH_CONFIG"; then
    sed -i "/^$BEGIN$/,/^$END$/d" "$SSH_CONFIG"
fi
{
    echo "$BEGIN"
    echo "Host $ALIAS"
    echo "    HostName $HOST_PART"
    echo "    User $USER_PART"
    for port in $PORTS; do
        # Target is the *server's* loopback: that is where the container publishes.
        echo "    LocalForward $port 127.0.0.1:$port"
    done
    echo "    ServerAliveInterval 30"
    echo "    ServerAliveCountMax 3"
    # Fail loudly instead of connecting without the forwards - otherwise the tunnel
    # looks fine and the error surfaces much later, in the overlay.
    echo "    ExitOnForwardFailure yes"
    echo "$END"
} >> "$SSH_CONFIG"
chmod 600 "$SSH_CONFIG"

# Checked before the service exists: a unit that cannot authenticate would just retry
# forever, and the reason would only be visible to whoever thinks to read the journal.
echo "🔑 Checking key-based login..."
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$ALIAS" true 2>/dev/null; then
    echo "⚠️  Cannot log in to $TARGET without a password." >&2
    echo "    Install your key:      ssh-copy-id $ALIAS" >&2
    echo "    First connection ever: ssh $ALIAS   (confirm the host key once)" >&2
    echo "    A passphrase on the key needs a running ssh-agent for the service." >&2
    exit 1
fi

echo "📄 Installing $UNIT..."
mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<EOF
[Unit]
Description=SSH tunnel to BugBot ($PORTS)
After=network-online.target
Wants=network-online.target

[Service]
# -N: forward only, no shell. Restart=always covers a dropped link; the
# ServerAliveInterval in ~/.ssh/config is what notices a dead one within ~90s.
ExecStart=/usr/bin/ssh -N $ALIAS
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

echo "🔄 Reloading systemd user units..."
systemctl --user daemon-reload

echo "🔌 Enabling lingering so the tunnel survives logout/reboot..."
loginctl enable-linger "$USER" || echo "   (skipped - not permitted here, the tunnel then runs only while logged in)"

echo "🚀 Starting bugbot-tunnel.service..."
systemctl --user enable --now bugbot-tunnel.service
systemctl --user restart bugbot-tunnel.service

echo "🔍 Checking the far end..."
for port in $PORTS; do
    reply=""
    for _ in $(seq 20); do
        reply="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$port/" || true)"
        [[ "$reply" != "000" && -n "$reply" ]] && break
        sleep 0.5
    done
    case "$reply" in
        401) echo "   $port ✅ reachable, and the token check is live" ;;
        000|"") echo "   $port ⚠️  nothing answers - is that listener enabled on the server?" ;;
        *)   echo "   $port ✅ reachable (HTTP $reply)" ;;
    esac
done

echo "✅ Done."
echo "   Status: systemctl --user status bugbot-tunnel"
echo "   Logs:   journalctl --user-unit=bugbot-tunnel.service -f"
echo "   Remove: ./disable-tunnel.sh"
