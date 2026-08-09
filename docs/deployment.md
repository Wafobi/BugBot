# Deployment

The bot runs in a container via [Podman](https://podman.io/docs/installation), with systemd
managing its lifecycle through Podman's **Quadlet** integration. Day-to-day operation is
`systemctl --user …` rather than juggling `podman run`/`stop`/`rm` by hand.

Running it directly with `python3 bugbot.py` works too and is the right thing for development.
Everything below is about the deployed setup.

## Prerequisites

Podman, from your package manager (`dnf install podman`, `apt install podman`, …).

If you hit a rootless build or run error mentioning subuid/subgid ranges, your user needs a
range registered once:

```bash
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$USER"
podman system migrate
```

---

## The scripts

| Script | Does |
|---|---|
| `./setup-systemd.sh` | **One-time setup.** Builds the image, installs the unit to `~/.config/containers/systemd/bugbot.container` with the real repo path filled in, reloads systemd, enables lingering, enables and starts `bugbot.service`. Safe to re-run — e.g. after moving the repo |
| `./start.sh` | Starts the service if it isn't running. Doesn't rebuild |
| `./update.sh` | **The day-to-day update.** Rebuilds the image, re-installs the Quadlet unit, restarts the service |
| `./disable-systemd.sh` | Reverses the setup: stops the service and removes the installed unit, so it no longer starts on boot. Leaves the image, `bugbot.db` and lingering untouched |

The first three exit early with a clear message if `.env` doesn't exist yet, or — for
`start.sh` and `update.sh` — if `setup-systemd.sh` hasn't been run.

`update.sh` re-installs the unit rather than just restarting, and that is not belt-and-braces:
`bugbot.container` names **every config file by full path** in its own `Volume=` line. If one of
them moves, the *installed* unit still points at the old path and the container refuses to start
with `statfs …: no such file or directory` — even though the repo is perfectly fine.

---

<a id="what-to-run-after-changing-what"></a>
## What to run after changing what

The `Dockerfile` does `COPY . .`, so **the code is baked into the image**, while the configs and
the database are mounted over it from your clone. That one difference decides everything:

| You changed | Run | Why |
|---|---|---|
| any `*.json` | **nothing** — but edit *in place*, see below | `LiveConfig` re-reads the file whenever its mtime changes — texts, names, timings and command names included |
| `.env` | `systemctl --user restart bugbot` | mounted, but only read once at process start |
| any `.py`, `requirements.txt`, `Dockerfile` | `./update.sh` | baked into the image — needs a rebuild |
| `bugbot.container` | `./update.sh` | the unit systemd reads is a *copy* in `~/.config/containers/systemd/` |

> ### The one that bites
>
> `git pull` followed by `systemctl --user restart bugbot` **looks** like it worked. The service
> comes back up, the logs look healthy — and it is still running the old code, because nothing
> rebuilt the image.
>
> **After pulling, always `./update.sh`.**

<a id="editing-in-place"></a>
> ### The other one that bites
>
> A bind mount of a **file** hangs off that file's inode, not its name. Overwriting the file is
> fine — the container is looking at the same inode and sees every byte you write. *Replacing*
> it is not: you get a new file under the old name, and the mount keeps pointing at the old one,
> which nobody edits any more. From then on the container sees **no** change, not this one and
> not any later one, no matter how often you edit or how long you wait.
>
> Plenty of ordinary things replace rather than overwrite: `vim` with the default `backupcopy`,
> VS Code (including Remote-SSH), `sed -i`, `mv`, and every `git pull` that touches the file.
> Nothing warns you — the file on the host looks exactly right, and so does the one in the
> container.
>
> ```bash
> python3 check_config.py    # on the host, with the service running
> ```
>
> compares both sides and names any config the container has lost track of. The fix is always
> `systemctl --user restart bugbot.service` — the mount is re-resolved at container start.
>
> To edit without tripping it: `nano` is safe as shipped, `vim` needs `:set backupcopy=yes`, and
> `cat > file` / `cp file.new file` overwrite in place. After a `git pull` you run `./update.sh`
> anyway, which restarts and therefore re-resolves everything.

`update.sh` ends with the restart, so you never need both. It is a strict superset of
`systemctl --user restart bugbot`, which is worth keeping only as the quick path when you know
nothing needs rebuilding — an `.env` edit, or recycling a wedged process. With
`requirements.txt` unchanged the `pip install` layer is cached, so a rebuild costs seconds.

---

## What's mounted, and why

`.env`, the `*.json` files and `bugbot.db` are bind-mounted rather than baked in, for two
reasons: it keeps secrets out of the image, and it means in-place edits survive container
restarts — including the automatic Twitch token refresh writing rotated tokens back into `.env`,
and every database write.

Note that **each config file gets its own `Volume=` line**. Without the mounts for the neutral
features' JSON (`moderation`, `stats`, `chat_log`, `sql_db`, `variables`), the version baked into the image
would be the effective one and edits on the host would quietly do nothing.

Three details in `bugbot.container` that exist because of a specific failure:

- **`UserNS=keep-id:uid=1000,gid=1000`** — without it, rootless Podman maps the calling host
  user to root inside the container, and the bot (uid 1000 per the `Dockerfile`) lands on a
  subuid that doesn't own the mounted `bugbot.db`. SQLite then reports "readonly database" and
  every feature needing storage fails at startup.
- **`:Z` on each volume** — relabels for SELinux. Needed on Fedora/RHEL-based systems, harmless
  elsewhere.
- **`Restart=always`, not `on-failure`** — the bot should come back even when the process exits
  cleanly with code 0, which happens if discord.py finally gives up and `main()` returns
  normally. `systemctl stop` is unaffected by the policy.

`setup-systemd.sh` `touch`es `bugbot.db` before the first build, because a bind-mount source that
doesn't exist yet gets created by Podman as a **directory**, which breaks `sqlite3.connect()`
inside the container.

`PublishPort=127.0.0.1:4456:4456` is the only inbound port — the OBS relay's end of the SSH
tunnel. Leave the line out entirely if you don't use OBS. See [OBS](obs.md#security).

---

## Doing it by hand

```bash
podman build -t bugbot .

mkdir -p ~/.config/containers/systemd
cp bugbot.container ~/.config/containers/systemd/
sed -i "s#/path/to/bugbot#$(pwd)#g" ~/.config/containers/systemd/bugbot.container

systemctl --user daemon-reload
systemctl --user enable --now bugbot.service

loginctl enable-linger "$USER"   # survive logout and reboots
```

From then on: `podman build -t bugbot .` after a code change, then
`systemctl --user restart bugbot`.

```bash
systemctl --user status bugbot
systemctl --user stop bugbot
```

---

## Logs

The bot logs to stdout, which Podman forwards to the journal:

```bash
journalctl --user-unit=bugbot.service -f
```

Note the `--user-unit=` form rather than the more familiar `--user -u bugbot`. The latter
restricts journalctl to *per-user journal files*, and systemd-journald only ever creates those
under `/var/log/journal`. On a host with no persistent journal — no `/var/log/journal`
directory, so journald keeps everything in RAM under `/run/log/journal` and doesn't split it per
user — `journalctl --user …` reports **"No journal files were found"** while the service is
happily logging.

`--user-unit=` matches on the `_SYSTEMD_USER_UNIT` field across every journal file the user can
read, so it works either way.

A volatile journal is also wiped on every reboot, so it's worth enabling persistent storage once,
as root:

```bash
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
```

After that the logs survive reboots, and `journalctl --user -u bugbot -f` starts working too.

### What healthy startup looks like

```
🧩 Feature 'sql_db' geladen (storage).
🧩 Feature 'moderation' geladen (moderation).
🔌 Plattform 'twitch' geladen (announce, chat, moderate, stream).
✅ Twitch-IRC verbunden
```

Features load before platforms — that's the correct order, see
[Architecture](architecture.md#startup).

---

<a id="troubleshooting"></a>
## Troubleshooting

Start here:

```bash
python3 check_credentials.py            # all configured platforms
python3 check_credentials.py twitch     # just one
```

It asks the services themselves — is the token still valid, are the scopes complete, is the
bot actually a moderator in that channel, are the privileged intents switched on, do the roles
and channels named in `discord.json` exist on the server. All read-only; nothing is changed.
Exit code 1 if anything failed, so it works in a pre-deploy check.

This matters because the bot **skips a platform it can't load** and keeps running with the
rest — correct behaviour, but it means an expired token is just one line in the log.

| Symptom | Cause |
|---|---|
| Code changes have no effect | The image wasn't rebuilt. Run `./update.sh`, not `restart` |
| `statfs …: no such file or directory` on start | A config file moved; the installed unit still points at the old path. `./update.sh` re-installs it |
| `readonly database`, storage features fail | The `UserNS=keep-id` line is missing or the image predates it |
| `sqlite3.connect()` fails in the container | `bugbot.db` didn't exist at first start and Podman made a directory. Remove it, `touch bugbot.db`, re-run `./setup-systemd.sh` |
| "No journal files were found" | Use `--user-unit=`; see above |
| Service dies and doesn't come back | Check `Restart=always` is in the *installed* unit, not just the repo copy |
| A platform is missing at startup | It was skipped with a warning — usually a missing token. The others keep running by design |
| Everything fails to start | If *no* platform loads, startup fails deliberately. Read the warnings above the error |

To check what the running container actually has:

```bash
podman exec bugbot cat /app/platforms/twitch/twitch.json | head
podman images | grep bugbot          # is the image as new as you think?
```

## See also

- [Configuration](configuration.md#environment-env) — every environment variable
- [OBS](obs.md#security) — why the port is loopback-only
