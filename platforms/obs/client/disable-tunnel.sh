#!/usr/bin/env bash
# Reverses setup-tunnel.sh: stops the tunnel, removes its systemd user service and
# takes the ~/.ssh/config entry back out. Runs on the OBS machine, like its counterpart.
#
# It undoes whichever of the two shapes setup-tunnel.sh chose, and the markers in the
# config are what say which one that was:
#
#   own tunnel      Our whole Host block goes, and so does bugbot-tunnel.service.
#
#   shared tunnel   Only the LocalForward lines between our inner markers go. The Host
#                   block itself, and whatever service dials it, belong to somebody else
#                   - that connection stays up, just without our two ports.
#
# A Host entry someone wrote by hand is never removed, because this script has no way to
# know what else it is used for. User lingering stays enabled too: setup-systemd.sh may
# rely on it on the same machine.
set -euo pipefail

SSH_CONFIG="$HOME/.ssh/config"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/bugbot-tunnel.service"
BEGIN="# >>> bugbot tunnel >>>"
END="# <<< bugbot tunnel <<<"
FWD_BEGIN="^[[:space:]]*# >>> bugbot forwards >>>$"
FWD_END="^[[:space:]]*# <<< bugbot forwards <<<$"

has_own_block()  { grep -qF "$BEGIN" "$SSH_CONFIG" 2>/dev/null; }
has_forwards()   { grep -qE "$FWD_BEGIN" "$SSH_CONFIG" 2>/dev/null; }

if [[ ! -f "$UNIT" ]] && ! has_own_block && ! has_forwards; then
    echo "ℹ️  Nothing installed by setup-tunnel.sh - nothing to remove."
    exit 0
fi

# The alias whose block carries our forwards - needed to find the service that dials it,
# so it can be restarted once the ports are gone from its config.
shared_alias() {
    awk -v fb="# >>> bugbot forwards >>>" '
        tolower($1) == "host" { host = $2 }
        index($0, fb) { print host; exit }
    ' "$SSH_CONFIG"
}

unit_for_alias() {
    local f last
    shopt -s nullglob
    for f in "$UNIT_DIR"/*.service; do
        [[ "$f" == "$UNIT" ]] && continue
        last="$(awk -F= '/^ExecStart=/{sub(/^ExecStart=/,""); print; exit}' "$f" | awk '{print $NF}')"
        [[ "$last" == "$1" ]] && { echo "$(basename "$f")"; return 0; }
    done
    return 1
}

SHARED_UNIT=""
if has_forwards; then
    ALIAS="$(shared_alias)"
    [[ -n "$ALIAS" ]] && SHARED_UNIT="$(unit_for_alias "$ALIAS" || true)"
fi

if [[ -f "$UNIT" ]]; then
    echo "🛑 Stopping bugbot-tunnel.service..."
    systemctl --user disable --now bugbot-tunnel.service 2>/dev/null || true

    echo "🗑️  Removing $UNIT..."
    rm -f "$UNIT"

    echo "🔄 Reloading systemd user units..."
    systemctl --user daemon-reload
fi

if has_own_block; then
    echo "🗑️  Removing our Host block from $SSH_CONFIG..."
    sed -i "/^$BEGIN$/,/^$END$/d" "$SSH_CONFIG"
    # setup-tunnel.sh separates its block from the next one with a blank line. Cutting
    # the block out leaves that blank behind, and a setup/disable cycle would otherwise
    # add one every time. Collapse runs of blanks back to a single one.
    sed -i '/./,/^$/!d' "$SSH_CONFIG"
fi

if has_forwards; then
    echo "🗑️  Removing our forwards from the 'Host ${ALIAS:-?}' block in $SSH_CONFIG..."
    sed -i "/$FWD_BEGIN/,/$FWD_END/d" "$SSH_CONFIG"
fi

if [[ -n "$SHARED_UNIT" ]]; then
    echo "🚀 Restarting $SHARED_UNIT so it drops the ports..."
    systemctl --user restart "$SHARED_UNIT"
    echo "✅ Tunnel removed - our ports are no longer forwarded, $SHARED_UNIT keeps its own."
else
    echo "✅ Tunnel removed - the ports are no longer forwarded."
fi
echo "   (Lingering and any hand-written ssh config were left untouched."
echo "    Run ./setup-tunnel.sh user@server to set it up again.)"
