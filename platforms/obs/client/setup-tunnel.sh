#!/usr/bin/env bash
# One-time setup of the SSH tunnel from the OBS machine to the bot.
#
# NOTE: this one runs on the *OBS machine*, not on the server - unlike every other
# script here. It belongs to the same reversed direction as obs_bridge.py next to it:
# the bot's listeners sit on the server's loopback and are deliberately not reachable
# from the internet, so the way to them is a tunnel dialled from this side.
#
#   4456  obs-websocket relay   (platforms/obs)
#   4457  overlay listener      (features/overlay)
#   4458  chat panel listener   (features/chat_panel)
#
# Two shapes exist, and the script decides between them by looking at what is already
# there - both are fine, they just differ in who owns the connection:
#
#   own tunnel      The default, and the simpler one: its own Host block plus a
#                   bugbot-tunnel.service, both owned by this script. Other tunnels to the
#                   same server are left alone - separate connections do not collide as
#                   long as they do not ask for the same ports.
#
#   shared tunnel   Some entry already forwards 4456/4457, because someone folded the bot
#                   into a tunnel of their own. Then this script stays out of the way: it
#                   adds only what is missing and lets the existing service keep dialling,
#                   since a second ssh asking for the same forwards would lose the bind
#                   and die on ExitOnForwardFailure. Point it at a block yourself with
#                   BUGBOT_TUNNEL_ALIAS.
#
#   ./setup-tunnel.sh user@server
#   BUGBOT_TUNNEL_PORTS="4456 4457 4458" ./setup-tunnel.sh user@server
#   BUGBOT_TUNNEL_ALIAS=myhost ./setup-tunnel.sh user@server   # pick the entry yourself
#
# Safe to re-run in either shape: blocks and forward lines are replaced, never stacked.
set -euo pipefail

PORTS="${BUGBOT_TUNNEL_PORTS:-4456 4457 4458}"
SSH_CONFIG="$HOME/.ssh/config"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/bugbot-tunnel.service"
BEGIN="# >>> bugbot tunnel >>>"
END="# <<< bugbot tunnel <<<"
# A second pair of markers, for the forwards we add *inside* somebody else's block.
# ssh_config has no inline comments, so the marker has to be its own line.
FWD_BEGIN="    # >>> bugbot forwards >>>"
FWD_END="    # <<< bugbot forwards <<<"

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

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$SSH_CONFIG"
# Compared again at the end. A re-run that changes nothing must not bounce the tunnel:
# on a shared one that would drop somebody else's forwards for a few seconds.
CONFIG_BEFORE="$(sha256sum < "$SSH_CONFIG")"

# --- reading what ssh actually thinks ------------------------------------------------
# Everything below asks `ssh -G` rather than parsing the file. That is the only way to
# see Match blocks, Include files and a catch-all "Host *" the way ssh will see them.

ssh_value() {  # ssh_value <alias> <keyword>  -> first value, lowercased keyword match
    ssh -G "$1" 2>/dev/null | awk -v k="$2" 'tolower($1)==k {print $2; exit}'
}

has_forward() {  # has_forward <alias> <port>
    ssh -G "$1" 2>/dev/null | awk -v p="$2" 'tolower($1)=="localforward" && $2==p {f=1} END{exit !f}'
}

config_aliases() {  # every literal name on a Host line; patterns cannot be dialled
    awk 'tolower($1)=="host"{for(i=2;i<=NF;i++) if ($i !~ /[*?!]/) print $i}' "$SSH_CONFIG" \
        | awk '!seen[$0]++'
}

# An entry to the same server that *already* carries our ports - someone folded the bot
# into a tunnel of their own. Adopting that one is not a preference but the only working
# option: a second ssh asking for the same forwards loses the bind and dies.
#
# Deliberately narrow. Merely reaching the same server is not enough to take over a block
# somebody else wrote - that guess is the operator's to make, via BUGBOT_TUNNEL_ALIAS.
find_alias() {
    local a hn un p ours
    while read -r a; do
        [[ -z "$a" ]] && continue
        hn="$(ssh_value "$a" hostname)"
        un="$(ssh_value "$a" user)"
        [[ "$hn" == "$HOST_PART" && "$un" == "$USER_PART" ]] || continue
        ours=1
        for p in $PORTS; do has_forward "$a" "$p" || ours=0; done
        [[ "$ours" == 1 ]] && { echo "$a"; return 0; }
    done < <(config_aliases)
    return 1
}

# A unit that already dials this alias - ours or somebody else's.
unit_for_alias() {  # unit_for_alias <alias>
    local f last
    shopt -s nullglob
    for f in "$UNIT_DIR"/*.service; do
        last="$(awk -F= '/^ExecStart=/{sub(/^ExecStart=/,""); print; exit}' "$f" | awk '{print $NF}')"
        [[ "$last" == "$1" ]] && { echo "$f"; return 0; }
    done
    return 1
}

# --- deciding the shape ---------------------------------------------------------------

OWN_BLOCK=0
if grep -qF "$BEGIN" "$SSH_CONFIG"; then
    OWN_BLOCK=1
fi

if [[ -n "${BUGBOT_TUNNEL_ALIAS:-}" ]]; then
    ALIAS="$BUGBOT_TUNNEL_ALIAS"
    # An explicit alias that does not exist yet means "write me one under this name".
    grep -qiE "^[[:space:]]*Host[[:space:]]+.*(^|[[:space:]])$ALIAS([[:space:]]|$)" "$SSH_CONFIG" \
        || OWN_BLOCK=1
elif [[ "$OWN_BLOCK" == 1 ]]; then
    ALIAS="$(awk -v b="$BEGIN" -v e="$END" '
        $0==b {inb=1; next} $0==e {inb=0} inb && tolower($1)=="host" {print $2; exit}' "$SSH_CONFIG")"
    ALIAS="${ALIAS:-bugbot}"
elif ALIAS="$(find_alias)"; then
    echo "🔎 Found an existing entry for $TARGET: Host $ALIAS"
else
    ALIAS="bugbot"
    OWN_BLOCK=1
fi

# --- writing the ssh config -----------------------------------------------------------

drop_range() {  # drop_range <begin-regex> <end-regex>
    sed -i "/$1/,/$2/d" "$SSH_CONFIG"
}

if [[ "$OWN_BLOCK" == 1 ]]; then
    echo "📄 Writing the '$ALIAS' entry to $SSH_CONFIG..."
    # Drop a previous block of ours before writing the new one, so re-running updates
    # instead of stacking a second Host with the same name.
    grep -qF "$BEGIN" "$SSH_CONFIG" && drop_range "^$BEGIN$" "^$END$"
    block="$(mktemp)"
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
    } > "$block"

    if grep -qE '^[[:space:]]*Host[[:space:]]+.*\*' "$SSH_CONFIG"; then
        # A catch-all block wins over everything below it, because ssh keeps the *first*
        # value it sees for each keyword. Appending to the end of the file would put our
        # ServerAlive*/ExitOnForwardFailure where a "Host *" has already answered.
        tmp="$(mktemp)"
        awk -v blockfile="$block" '
            function dump(  line) { while ((getline line < blockfile) > 0) print line; close(blockfile) }
            function flush(  i) { for (i = 1; i <= nb; i++) print B[i]; nb = 0 }
            # Comment lines directly above the catch-all are its header and have to stay
            # with it. A blank line ends that run: what is above the blank introduced the
            # previous section, so it must not be dragged along below our block.
            !done && $0 ~ /^[[:space:]]*$/ { flush(); print; next }
            !done && $0 ~ /^[[:space:]]*#/ { B[++nb] = $0; next }
            !done && tolower($1) == "host" && $0 ~ /\*/ { dump(); print ""; flush(); done = 1; print; next }
            { flush(); print }
            END { flush(); if (!done) dump() }
        ' "$SSH_CONFIG" > "$tmp" && mv "$tmp" "$SSH_CONFIG"
    else
        cat "$block" >> "$SSH_CONFIG"
    fi
    rm -f "$block"
else
    # Somebody else's block. Only the forwards go in, between our own markers so
    # disable-tunnel.sh can take exactly those back out - the rest is not ours to touch.
    # Our own lines go first, before anything is measured. Otherwise a re-run sees the
    # ports it added last time, concludes there is nothing to do, and still drops the
    # block - leaving the tunnel without the forwards it just reported as fine.
    grep -qE "^[[:space:]]*# >>> bugbot forwards >>>$" "$SSH_CONFIG" \
        && drop_range "^[[:space:]]*# >>> bugbot forwards >>>$" "^[[:space:]]*# <<< bugbot forwards <<<$"

    # What is left is what the block carries on its own, so only genuinely absent ports
    # are added and a port somebody else already forwards is never duplicated.
    MISSING=""
    for port in $PORTS; do
        has_forward "$ALIAS" "$port" || MISSING="$MISSING $port"
    done

    if [[ -n "${MISSING# }" ]]; then
        tmp="$(mktemp)"
        awk -v alias="$ALIAS" -v fwds="${MISSING# }" -v fb="$FWD_BEGIN" -v fe="$FWD_END" '
            function emit(  i) {
                if (done) return
                print fb
                for (i = 1; i <= n; i++) print "    LocalForward " P[i] " 127.0.0.1:" P[i]
                print fe
                done = 1
            }
            function flush(  i) { for (i = 1; i <= nb; i++) print B[i]; nb = 0 }
            BEGIN { n = split(fwds, P, " ") }
            {
                # ssh ends a block at the next Host, but a human reads it as ending at the
                # last directive: blank lines and comments after that usually introduce the
                # *next* block. Hold them back so the insert lands on the last directive
                # and those lines stay with what they describe. A comment followed by more
                # directives is flushed again below, keeping its original position.
                if (inb && ($0 ~ /^[[:space:]]*$/ || $0 ~ /^[[:space:]]*#/)) { B[++nb] = $0; next }
                if (tolower($1) == "host") {
                    if (inb) { emit(); inb = 0 }
                    flush()
                    for (i = 2; i <= NF; i++) if ($i == alias) inb = 1
                } else flush()
                print
            }
            END { if (inb) emit(); flush() }
        ' "$SSH_CONFIG" > "$tmp" && mv "$tmp" "$SSH_CONFIG"
    fi
fi
chmod 600 "$SSH_CONFIG"
CONFIG_CHANGED=0
[[ "$(sha256sum < "$SSH_CONFIG")" != "$CONFIG_BEFORE" ]] && CONFIG_CHANGED=1

# Said only now, because "added" is a claim about the file and the file is what decides:
# a re-run rewrites our own lines byte for byte and has in fact changed nothing.
if [[ "$OWN_BLOCK" != 1 ]]; then
    if [[ "$CONFIG_CHANGED" == 0 ]]; then
        echo "📄 'Host $ALIAS' already forwards $PORTS - config unchanged."
    elif [[ -n "${MISSING# }" ]]; then
        echo "📄 Added${MISSING} to the existing 'Host $ALIAS' block."
    else
        echo "📄 Dropped our forward lines from 'Host $ALIAS' - it carries those ports itself now."
    fi
fi

# Not written into a foreign block: ExitOnForwardFailure decides what a *shared* tunnel
# does when one port is busy, and killing somebody else's forwards is not our call.
for opt in serveraliveinterval:30 exitonforwardfailure:yes; do
    key="${opt%:*}"; want="${opt#*:}"
    got="$(ssh_value "$ALIAS" "$key")"
    if [[ "$got" != "$want" ]]; then
        echo "ℹ️  $ALIAS has ${key}=${got:-unset}, not $want."
        echo "    Without it a dead link or a busy port can go unnoticed - consider adding"
        echo "    it to that Host block, or to a 'Host *' block at the end of the file."
    fi
done

# --- login check ----------------------------------------------------------------------
# Checked before the service exists: a unit that cannot authenticate would just retry
# forever, and the reason would only be visible to whoever thinks to read the journal.
echo "🔑 Checking key-based login..."
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes "$ALIAS" true 2>/dev/null; then
    echo "⚠️  Cannot log in to $TARGET without a password." >&2
    echo "    Install your key:      ssh-copy-id $ALIAS" >&2
    echo "    First connection ever: ssh $ALIAS   (confirm the host key once)" >&2
    echo "    A passphrase on the key needs a running ssh-agent for the service." >&2
    exit 1
fi

# --- the service ----------------------------------------------------------------------

mkdir -p "$UNIT_DIR"
FOREIGN_UNIT=""
if EXISTING="$(unit_for_alias "$ALIAS")" && [[ "$EXISTING" != "$UNIT" ]]; then
    FOREIGN_UNIT="$(basename "$EXISTING")"
fi

if [[ -n "$FOREIGN_UNIT" ]]; then
    echo "🔗 $FOREIGN_UNIT already dials '$ALIAS' - reusing it instead of adding a"
    echo "   second ssh with the same forwards."
    # Ours would be that second ssh. If a previous run left one behind, it has to go.
    if [[ -f "$UNIT" ]]; then
        echo "🗑️  Removing the now-redundant bugbot-tunnel.service..."
        systemctl --user disable --now bugbot-tunnel.service 2>/dev/null || true
        rm -f "$UNIT"
        systemctl --user daemon-reload
    fi
    # Somebody else's service: bounce it only when it would actually change something.
    if [[ "$CONFIG_CHANGED" == 1 ]]; then
        echo "🚀 Restarting $FOREIGN_UNIT so it picks up the new forwards..."
        systemctl --user restart "$FOREIGN_UNIT"
    elif ! systemctl --user is-active --quiet "$FOREIGN_UNIT"; then
        echo "🚀 Starting $FOREIGN_UNIT..."
        systemctl --user start "$FOREIGN_UNIT"
    else
        echo "✅ $FOREIGN_UNIT is up and its config is unchanged - leaving it alone."
    fi
else
    WANT_UNIT="$(cat <<EOF
[Unit]
Description=SSH tunnel to BugBot ($PORTS)
# network-online.target does not exist in the systemd *user* manager, so ordering
# against it would silently do nothing. Restart=always is what covers a boot that
# gets here before the network does.
After=default.target

[Service]
# -N: forward only, no shell. Restart=always covers a dropped link; the
# ServerAliveInterval in ~/.ssh/config is what notices a dead one within ~90s.
ExecStart=/usr/bin/ssh -N $ALIAS
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
)"

    if [[ -f "$UNIT" && "$(cat "$UNIT")" == "$WANT_UNIT" ]]; then
        echo "✅ $UNIT is already up to date."
    else
        echo "📄 Installing $UNIT..."
        printf '%s\n' "$WANT_UNIT" > "$UNIT"
        echo "🔄 Reloading systemd user units..."
        systemctl --user daemon-reload
        UNIT_CHANGED=1
    fi

    echo "🔌 Enabling lingering so the tunnel survives logout/reboot..."
    loginctl enable-linger "$USER" || echo "   (skipped - not permitted here, the tunnel then runs only while logged in)"

    systemctl --user enable bugbot-tunnel.service >/dev/null 2>&1 || true
    if [[ "${UNIT_CHANGED:-0}" == 1 || "$CONFIG_CHANGED" == 1 ]]; then
        echo "🚀 Restarting bugbot-tunnel.service..."
        systemctl --user restart bugbot-tunnel.service
    elif ! systemctl --user is-active --quiet bugbot-tunnel.service; then
        echo "🚀 Starting bugbot-tunnel.service..."
        systemctl --user start bugbot-tunnel.service
    else
        echo "✅ bugbot-tunnel.service is up and unchanged - leaving it alone."
    fi
fi

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

SERVICE="${FOREIGN_UNIT:-bugbot-tunnel.service}"
echo "✅ Done - forwards on 'Host $ALIAS', carried by $SERVICE."
echo "   Status: systemctl --user status ${SERVICE%.service}"
echo "   Logs:   journalctl --user-unit=$SERVICE -f"
echo "   Remove: $(dirname "$0")/disable-tunnel.sh"
