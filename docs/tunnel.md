# The SSH tunnel

Two of the bot's listeners are reached from outside the server, and neither is exposed to the
internet to make that possible. The way in is an SSH tunnel, dialled **from the OBS machine**.

| Port | Listener | Used by |
|---|---|---|
| `4456` | obs-websocket relay | [`platforms/obs`](obs.md) |
| `4457` | overlay listener | [`features/overlay`](overlay.md) |

Everything here runs on the OBS machine — the same end that runs OBS itself. That is unusual for
this repo, where every other script runs on the server, and it follows from the direction of the
connection rather than from preference.

---

## Why it points that way

The bot publishes both ports to the **server's loopback only**
(`PublishPort=127.0.0.1:4456:4456` in `bugbot.container`). Nothing outside that machine can open
them, which is the point: a listener that answers strangers is a listener someone will eventually
knock on. The tokens are a second lock, not the first one.

So the OBS machine cannot dial the bot. It dials **ssh**, and the forwarded port comes out on its
own localhost — which is why the relay and the browser source are both configured against
`127.0.0.1`. The tunnel also encrypts the obs-websocket leg, which the protocol itself leaves in
the clear.

```
OBS machine                                  server
┌──────────────────────┐                    ┌──────────────────────┐
│ relay ──► 127.0.0.1:4456 ══ ssh -L ══════► 127.0.0.1:4456        │
│ browser ► 127.0.0.1:4457 ══ ssh -L ══════► 127.0.0.1:4457        │
└──────────────────────┘   encrypted        └──────────────────────┘
```

---

## When both ends are the same machine

**Then you do not need any of this.** Skip `setup-tunnel.sh` entirely.

The tunnel exists to cross a network, and if the bot and OBS sit on one box there is nothing to
cross: the container already publishes `4456`/`4457` to that machine's loopback, which is exactly
where the relay and the browser source look. Running the script anyway would build an ssh
connection from localhost to localhost and then fail to bind ports that are already answering.

Nothing else changes. In particular the two tokens are still required — they are what makes the
listeners exist at all, and `OBS_BRIDGE_TOKEN` is still checked on every handshake. "Local" is
not a trust boundary here; the loopback bind is.

One thing to watch: running the bot **outside** a container on that machine, set
`OBS_BRIDGE_BIND=127.0.0.1` and `OVERLAY_BIND=127.0.0.1`. The defaults bind `0.0.0.0`, which is
correct only because a container's network namespace makes the host-side `PublishPort` the real
restriction. Without the container, that restriction is gone and the default would expose both
listeners to your whole network.

Everything below is for the split setup.

---

## Setting it up

```bash
platforms/obs/client/setup-tunnel.sh user@server
```

That writes an `~/.ssh/config` entry, installs a **systemd user service** so the tunnel comes back
after a dropped link and a reboot, and then checks that the far end actually answers before it
calls itself done. `disable-tunnel.sh` next to it reverses exactly what it did.

Re-running is safe and is the normal way to change something: blocks and forward lines are
replaced, never stacked. A re-run that finds everything already in place changes nothing and, in
particular, does **not** bounce the tunnel.

| Variable | Default | For |
|---|---|---|
| `BUGBOT_TUNNEL_PORTS` | `4456 4457` | running only one of the two listeners |
| `BUGBOT_TUNNEL_ALIAS` | `bugbot` | naming the `~/.ssh/config` entry, or pointing the script at an existing one |

### Two shapes

Which one you get is decided by what is already in your config, not by a flag:

**Its own tunnel** — the default, and the simpler one. The script owns a `Host bugbot` block and
a `bugbot-tunnel.service`, and nothing else on the machine is involved. Other tunnels to the same
server are left alone: separate ssh connections coexist fine, as long as they do not ask for the
same ports.

**A shared tunnel** — you already forward 4456 and 4457 through a tunnel of your own, perhaps
alongside unrelated ports to the same machine. The script detects this and stays out of the way:
it adds only what is missing, between its own marker comments, and leaves the connection to
whichever service already dials it.

That second case is not a convenience. Two ssh processes asking for the same local port cannot
both win — the second one fails to bind, and `ExitOnForwardFailure yes` then makes it exit rather
than sit there half-working. Reusing the existing connection is the only thing that works.

To fold the bot into a tunnel that does *not* carry its ports yet, name it explicitly:

```bash
BUGBOT_TUNNEL_ALIAS=my-tunnel ./setup-tunnel.sh user@server
```

Without that, a hand-written block is never taken over. Deciding that two tunnels are really one
is a judgement about your setup, and the script does not make it for you.

---

## What the service looks like

```ini
[Service]
ExecStart=/usr/bin/ssh -N bugbot
Restart=always
RestartSec=10
```

`-N` means forward only, no shell. The rest — the forwards, the keepalive — lives in
`~/.ssh/config`, so the unit stays readable and `ssh bugbot` by hand does the same thing.

Three details that are easy to get wrong:

- **`After=network-online.target` does nothing here.** That target does not exist in the systemd
  *user* manager, so ordering against it is silently ignored. `Restart=always` with `RestartSec=10`
  is what actually covers a boot that gets there before the network does — the unit fails, waits
  ten seconds, and succeeds once there is a route.
- **Lingering** is what makes a user service start at boot instead of at login. `setup-tunnel.sh`
  enables it (`loginctl enable-linger`); without it the tunnel only exists while you are logged in.
- **`ServerAliveInterval 30` / `ServerAliveCountMax 3`** are what notice a *dead* link, within
  about 90 seconds. Without them ssh keeps a connection that no longer carries anything, systemd
  sees a healthy process, and `Restart=always` never fires. A tunnel that is down but not dead is
  the failure mode this guards against.

If you keep a catch-all `Host *` block, note that ssh takes the **first** value it sees for each
keyword — so a catch-all belongs at the end of the file, below the specific blocks. The script
inserts its own block above any catch-all for that reason.

---

## Checking it

```bash
systemctl --user status bugbot-tunnel
journalctl --user-unit=bugbot-tunnel.service -f
ssh -G bugbot | grep -E 'localforward|serveralive|exitonforward'   # what ssh really resolves
ss -tlnp | grep -E ':(4456|4457) '                                 # what is actually listening
```

`ssh -G` is worth knowing: it prints the options ssh will use after `Include`s, `Match` blocks and
catch-alls have been applied. Reading the config file yourself will eventually disagree with it.

| Symptom | Likely cause |
|---|---|
| `bind: Address already in use`, unit restart-loops | another tunnel already forwards that port — see *shared tunnel* above |
| Unit runs, nothing answers | the tunnel is up but the listener is not; check `OBS_BRIDGE_TOKEN` / `OVERLAY_TOKEN` on the server |
| `Permission denied (publickey)` in the journal | `ssh-copy-id bugbot`; a passphrase on the key needs a running ssh-agent |
| Works until you log out | lingering is off — `loginctl enable-linger $USER` |
| Config edits have no effect | something above them already answered; check with `ssh -G` |

The setup script's own check tells the two halves apart: `401` means the port is reachable **and**
the token check is live, which is the healthy answer. Nothing at all means the tunnel arrived
somewhere that is not listening.
