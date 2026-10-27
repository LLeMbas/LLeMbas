# Deployment

Installs LLeMbas as a **system** service behind nginx with a self-signed
certificate. Written for a systemd + nginx host; tested on Debian.

| | Default |
|---|---|
| Service user | `lembas` (system account, `nologin`) |
| Home | `/home/lembas` |
| Install prefix | `/srv/lembas` (bind mount of the home) |
| Checkout | `$PREFIX/app` |
| Virtualenv | `$PREFIX/venv` |
| Database | `$PREFIX/data/lembas.db` |
| Environment | `$PREFIX/lembas.env` (mode 600) |
| Unit | `/etc/systemd/system/lembas.service` |
| Vhost | `/etc/nginx/conf.d/<host>.conf` |
| Listens on | `127.0.0.1:8080` — reachable only through nginx |

The prefix defaults to a bind mount of the service user's home because on many
machines the root filesystem is small while `/home` is not, and the virtualenv
plus database belong on the larger volume. Set `PREFIX=$HOME_DIR` to skip it.

## First install

```bash
SITE_HOST=chat.example ./deploy/install.sh
```

Idempotent — safe to re-run. It creates the user and bind mount, clones the
repo, builds the venv, generates `lembas.env` with a fresh `LEMBAS_SECRET_KEY`,
installs the unit and vhost, issues a self-signed certificate, adds a
`/etc/hosts` entry if the name does not already resolve, and enables the
service.

Then open `https://<SITE_HOST>`, accept the certificate warning, and create the
first account — it becomes the administrator.

Everything is overridable from the environment:

| Variable | Default | |
|---|---|---|
| `SITE_HOST` | `lembas.local` | nginx `server_name` and certificate CN |
| `APP_PORT` | `8080` | loopback port the service binds |
| `SERVICE_USER` | `lembas` | system account to run as |
| `HOME_DIR` | `/home/lembas` | that account's home |
| `PREFIX` | `/srv/lembas` | install root (bind mount of `HOME_DIR`) |
| `REPO_URL` | this checkout's `origin` | so a fork deploys itself. **Must be https** — see below |
| `LEMBAS_BRANCH` | `main` | branch to fetch, and what the `edge` channel follows |
| `LEMBAS_CHANNEL` | `stable` | `stable` follows release tags, `edge` follows the branch tip |
| `INSTALL_UPDATE_HELPER` | `0` | `1` lets the web interface deploy that branch as root |

**The deployment fetches over HTTPS, on purpose.** The service user has no SSH
key and should not have one: a credential that can push to the repository,
sitting on a box, to do a read-only job. If you push over SSH your checkout's
`origin` is an `ssh://` URL, which is the one thing that cannot work here — so
the installer refuses it and names the fix rather than letting the clone fail
with `Permission denied (publickey)` from an account you were not thinking about.

## Channels

| | follows | for |
|---|---|---|
| `stable` (default) | the newest `vX.Y.Z` tag of the running major version | anybody running this |
| `edge` | the tip of `LEMBAS_BRANCH` | whoever is building it |

**A branch tip is not a release.** Following `main` means deploying whatever was
pushed five minutes ago, possibly mid-feature — right for development and wrong
for a machine somebody depends on. Stable is the default for that reason.

**Stable never crosses a major version.** A 1.x host is offered 1.x releases
only, however new a 2.0 is: a new major may change what lies underneath (2.0
starts on a new database), so moving to one is done by hand, after exporting
what you want to keep. The Updates page says when a newer major is out. Root's
copy of `update.sh` holds the same line; a copy installed by an older release
does not, and the Updates page says so — re-run the installer with
`INSTALL_UPDATE_HELPER=1` to replace it.

A tag with a suffix (`v1.1.0-rc1`) is deliberately **not** a release: git's
version sort puts it *above* `v1.1.0`, so accepting one would step a stable host
onto a release candidate on the strength of a hyphen. A prerelease is something
you check out by name.

Release notes travel inside **annotated** tags, so `git tag -a v1.1.0 -m "…"` is
what puts them on the update page. Tags here are **signed** (`tag.gpgSign`), and
the notes render the same either way — `updates._notes_for` cuts the
`-----BEGIN SSH SIGNATURE-----` block off `%(contents)`, which would otherwise be
forty lines of base64 on the page. No forge API is involved anywhere — which
matters more than it sounds: a token on the deployment host to answer a
read-only question about version numbers is a bad trade, and it would tie this
to one forge.

## Updating from the web interface

`/admin/updates` says what is running (`git describe`, so `1.0.0` at a tag and
`1.0.0-7-gd4f56d` seven commits past one), what the channel offers, the release
notes, and the commits between. **Checking** reaches the remote; opening the page
does not.

The button is opt-in, and the reason is a boundary rather than caution:

```bash
INSTALL_UPDATE_HELPER=1 SITE_HOST=chat.example ./deploy/install.sh
```

That installs `lembas-update.path` and `lembas-update.service`, and puts a
**root-owned copy** of `update.sh` at `/usr/local/lib/lembas/update.sh`. The web
interface writes `$PREFIX/data/update-requested`; the path unit notices and the
service runs that copy **as root**, on the configured channel.

**Why a copy.** `install.sh` clones the checkout *as the service user*, so a
unit pointing inside it would have root executing a file the unprivileged
account can rewrite, and one that every update replaces with whatever the
branch contained. Either turns a compromise of the web application into root,
and the second needs no compromise at all. The cost is that changing
`update.sh` needs the installer re-run; the script tells you when its copy has
fallen behind, and says so loudly if it finds itself running from inside the
checkout.

**What that grants.** Anybody who can administer this web interface can then
deploy whatever is on the configured branch and restart the service. That is the
point of it, and it is why it is not the default.

**What it deliberately does not grant.** The request file carries nothing that
reaches a command line — no ref, no branch, no channel, no arguments, and its
*contents* are never read at all. Both are baked into the unit at install time,
so the button is always "deploy the channel this host was configured with" and
never "deploy something else". Re-running the installer without the flag removes
both units, the marker and the root-owned copy, and the page goes back to
printing the manual command.

A re-run **keeps the channel this host already follows** rather than resetting it
to `stable`: the channel is declared in `lembas.env` and in the unit, a re-run
keeps the first while rewriting the second, and an installer that silently moved
one half was causing exactly the mismatch the Updates page detects.

Without the helper the page says so and shows `sudo …/deploy/update.sh`, which is
the same honest degradation the search extra has.

## Updating by hand

```bash
sudo /srv/lembas/app/deploy/update.sh
```

`update.sh` fetches, moves the deployment checkout to what the channel offers
(the newest release tag of the running major, or the tip of the branch on
`edge`), reinstalls
dependencies and restarts, printing the commits it pulled. The checkout is
reset rather than merged on purpose: nothing is ever edited in place there, so
there is no local work to preserve and no conflicts to resolve.

## In a container

A `Dockerfile` and a `docker-compose.yml` are in the repository root. The
compose file **builds the image locally** from the checkout:

```bash
git clone https://github.com/LLeMbas/LLeMbas.git && cd LLeMbas
echo "LEMBAS_SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')" > .env
docker compose up -d --build
```

Updating is the same build from a newer checkout:

```bash
git pull && docker compose up -d --build
```

It publishes on `127.0.0.1:8080` and expects **a TLS reverse proxy in front**.
That is a constraint, not a preference: a service worker and a microphone both
require HTTPS or localhost, so over plain http on a LAN address the app cannot be
installed and cannot dictate — and the session cookie is deliberately not marked
`secure`, so an attacker on that network could steal a session.

Three things about the image:

- **No secret key is baked in**, and compose refuses to start without one. A key
  in an image is a key every copy of that image shares, and rotating it signs
  everybody out *and* makes stored upstream API keys unreadable.
- **`.git` is excluded**, so `/admin/updates` inside a container says it was not
  installed from a checkout and offers nothing. That is correct: a container is
  updated by rebuilding it from an updated checkout, as above.
- **One replica.** The generation registry, the stop mechanism, the terminal
  sessions and the schedule ticker are all in-process — two would mean two
  tickers and every schedule firing twice.

## On Proxmox

```bash
CTID=140 SITE_HOST=chat.example ./deploy/lxc-install.sh
```

Run on the Proxmox host. It creates an **unprivileged** Debian container,
installs the dependencies, and runs `deploy/install.sh` inside it — the same
installer, so a fix there reaches this without anybody remembering. Unprivileged
is not a default to change: nothing LLeMbas does needs privilege, because agent
chats run their commands on a linked device — some *other* machine.

## Operating it

```bash
systemctl status lembas
journalctl -u lembas -f
sudo -u lembas /srv/lembas/venv/bin/lembas-server info   # paths and counts
```

Configuration lives in `$PREFIX/lembas.env`. Edit it and restart.

## Notes

**The secret key is generated once.** `install.sh` will not overwrite an
existing `lembas.env`. Rotating `LEMBAS_SECRET_KEY` signs every user out *and*
makes stored upstream API keys unreadable — they would have to be re-entered.

**nginx buffering is off for a reason.** Replies stream as server-sent events.
With `proxy_buffering on` (the default) nginx holds the entire reply and
delivers it in one lump at the end, which is indistinguishable from streaming
being broken. `proxy_read_timeout` is raised to an hour because a model can
think for minutes before the first token.

**The vhost passes WebSocket upgrades through, and must.** The terminal panel
and linked devices use WebSockets. A `location` that sets `Connection ""` — which
is what SSE alone needs — fails every
handshake, and a failed handshake tells the browser nothing: no status, no
reason. The `map $http_upgrade` at the top of the vhost yields the empty string
when the client did not ask to upgrade, so streaming is unaffected. `update.sh`
warns when the installed vhost has drifted from the template, because this is
the failure most likely to be diagnosed as a bug in the application.

**Every restart kills every open shell.** A reply being written is persisted
with whatever it has; a terminal has nothing to persist, so a command still
running on the far side is cut off. `update.sh` restarts unconditionally, so a
deploy in the middle of somebody's `apt-get dist-upgrade` ends it. The panel is
told why rather than silently reconnecting to a new shell, which would have
lost the working directory and the half-typed command.

**A terminal is not in the transcript, and is not logged.** The open and the
close are logged with the user, the chat and the connection; what was typed is
not recorded anywhere. That follows from the design — the chat's mode governs
the model, not the person at the keyboard — but everything else an agent chat
does *is* in the transcript, so it is a difference in kind and worth knowing
before somebody goes looking for the history.

**Nothing an agent does runs on this machine.** Agent chats execute their
commands on a linked device: a machine running the
[LLeMbas CLI](https://github.com/LLeMbas/LLeMbas-CLI), signed in to this
instance by its owner — a container, a VM, a workstation. That is the whole
isolation story, and it is why the unit can stay locked down instead of being
opened up to make room for a sandbox.

`ProtectSystem=full` rather than `strict` only because the data directory must
be writable and `strict` would mean listing every path.

The practical consequence for whoever runs this: **the security of an agent
chat is the security of the device it runs on**, within the limits that device
sets for remote work. A throwaway container with the one project in it is a
very different thing from a production server, and LLeMbas cannot tell them
apart.

**Use a real certificate if this is exposed beyond a trusted LAN.** The
self-signed cert exists so the install works with no external dependencies;
point `ssl_certificate` at a real one and nothing else needs to change.

The session cookie is deliberately not marked `secure`, so that a LAN install
over plain http can sign anybody in at all. That has always meant a network
attacker on http could steal a session; with the terminal it also means they
could open an interactive shell on the machine behind that chat. If the
terminal is switched on, run this over TLS.

**One worker only.** True of generations already — the registry is in-process —
and sharper here: with two workers a browser reconnecting to its terminal could
land in the process that has no shell for it, and silently open a second one on
the same machine.
