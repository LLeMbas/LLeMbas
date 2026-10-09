#!/usr/bin/env bash
# Install LLeMbas as a system service behind nginx with a self-signed cert.
#
# Creates a dedicated service user, a virtualenv, a systemd unit and an nginx
# vhost. Idempotent: safe to re-run. To deploy new code afterwards use
# update.sh, which is what a `git push` should be followed by.
#
# Everything is configurable from the environment:
#
#   SITE_HOST=chat.example   ./deploy/install.sh    # vhost name
#   APP_PORT=8080                                   # loopback port
#   PREFIX=/srv/lembas                              # install root
#   HOME_DIR=/home/lembas                           # service user's home
#   REPO_URL=...                                    # defaults to this checkout's origin
#
# PREFIX defaults to a bind mount of HOME_DIR rather than living directly under
# /srv, because on many machines the root filesystem is small and the venv plus
# database belong on the larger /home volume. Set PREFIX=HOME_DIR to skip that.
set -euo pipefail

HERE="$(dirname "$(readlink -f "$0")")"

SITE_HOST="${SITE_HOST:-lembas.local}"
APP_PORT="${APP_PORT:-8080}"
SERVICE_USER="${SERVICE_USER:-lembas}"
HOME_DIR="${HOME_DIR:-/home/lembas}"
PREFIX="${PREFIX:-/srv/lembas}"
BRANCH="${LEMBAS_BRANCH:-main}"
# Which channel this host follows: `stable` (the newest release tag) or `edge`
# (the branch tip). Stable by default, because a branch tip is not a release --
# following one means deploying whatever was pushed five minutes ago, which is
# right for whoever builds this and wrong for whoever runs it.
# On a **re-run**, default to what this host already follows rather than to
# `stable`. The channel lives in two places -- `lembas.env`, which the page
# reads, and the systemd unit, which the button obeys -- and a re-run keeps the
# env file ("keeping it, and its secret key") while rewriting the unit. So a
# re-run to fix something unrelated silently moved one half and not the other,
# and left the host with a page naming one channel and a button deploying
# another. That mismatch has an alert of its own; an installer that *causes* it
# is the wrong end to be detecting it from.
#
# Parsed, not sourced -- `lembas.env` holds the secret key, and there is no
# reason for this to have it in a variable.
_installed_channel=""
if [[ -f "$PREFIX/lembas.env" ]]; then
  _installed_channel=$(sed -n 's/^LEMBAS_UPDATE_CHANNEL=\([a-z]\{1,16\}\)$/\1/p' \
    "$PREFIX/lembas.env" | tail -1)
fi
CHANNEL="${LEMBAS_CHANNEL:-${_installed_channel:-stable}}"
# Whether to install the units that let the web interface update this host.
# Off, and off on a re-run that does not ask for it: it grants anybody who can
# administer the web UI the ability to deploy the branch, as root. See the
# "Updating from the web interface" section of deploy/README.md.
INSTALL_UPDATE_HELPER="${INSTALL_UPDATE_HELPER:-0}"
# Default to wherever this checkout came from, so a fork deploys itself.
REPO_URL="${REPO_URL:-$(git -C "$HERE" remote get-url origin 2>/dev/null || true)}"

APP="$PREFIX/app"
VENV="$PREFIX/venv"
ENV_FILE="$PREFIX/lembas.env"

if [[ -z "$REPO_URL" ]]; then
  echo "Could not determine REPO_URL. Set it explicitly." >&2
  exit 1
fi

# The deployment clones as the service user, which has no SSH key and should not
# have one: a credential that can push to the repository, sitting on a box, to
# do a read-only job. Whoever runs this usually has an ssh:// origin because
# *they* push over SSH, so the default inherited from their checkout is the one
# thing that cannot work here.
#
# The clone would fail loudly anyway. Saying so first turns "Permission denied
# (publickey)" from the service user into a sentence that names the fix.
if [[ "$REPO_URL" == ssh://* || "$REPO_URL" == git@* ]]; then
  echo "== repository ==" >&2
  echo "   $REPO_URL is an SSH URL, and $SERVICE_USER has no key." >&2
  echo "   Set an https URL, which is what a deployment should fetch over:" >&2
  echo "     REPO_URL=https://host/owner/repo.git $0" >&2
  echo "   (Or give $SERVICE_USER a read-only deploy key and re-run.)" >&2
  exit 1
fi

echo "== plan =="
echo "   host    : https://$SITE_HOST  ->  127.0.0.1:$APP_PORT"
echo "   user    : $SERVICE_USER ($HOME_DIR)"
echo "   prefix  : $PREFIX"
echo "   repo    : $REPO_URL ($BRANCH, $CHANNEL channel)"
if [[ "$INSTALL_UPDATE_HELPER" == "1" ]]; then
  echo "   updates : web interface may deploy $BRANCH as root (helper units)"
else
  echo "   updates : by hand only ($PREFIX/app/deploy/update.sh)"
fi

echo "== service user =="
# --system: no ageing, no mail spool. Home under /home, not /var/lib, so the
# venv and database sit on the larger volume.
#
# `/usr/sbin/nologin` resolves on Debian and on distributions whose /usr/sbin
# is a symlink to /usr/bin alike; `/usr/bin/nologin` does not exist on Debian.
if ! getent passwd "$SERVICE_USER" >/dev/null; then
  sudo useradd --system --create-home --home-dir "$HOME_DIR" \
       --shell /usr/sbin/nologin --comment "LLeMbas" "$SERVICE_USER"
else
  echo "   user $SERVICE_USER already exists"
fi
sudo chmod 755 "$HOME_DIR"

if [[ "$PREFIX" != "$HOME_DIR" ]]; then
  echo "== $PREFIX bind-mount onto $HOME_DIR =="
  sudo mkdir -p "$PREFIX"
  grep -q "^$HOME_DIR[[:space:]]" /etc/fstab \
    || echo "$HOME_DIR  $PREFIX  none  bind  0 0" | sudo tee -a /etc/fstab >/dev/null
  sudo systemctl daemon-reload
  mountpoint -q "$PREFIX" || sudo mount "$PREFIX"
fi

echo "== checkout =="
if [[ ! -d "$APP/.git" ]]; then
  sudo -u "$SERVICE_USER" git clone --branch "$BRANCH" "$REPO_URL" "$APP"
else
  echo "   already cloned; use update.sh to pull"
fi

echo "== virtualenv =="
# `python3`, not `python`: on Debian the bare name does not exist unless
# `python-is-python3` is installed.
if [[ ! -x "$VENV/bin/python" ]]; then
  sudo -u "$SERVICE_USER" python3 -m venv "$VENV"
fi
sudo -u "$SERVICE_USER" "$VENV/bin/pip" install --quiet --upgrade pip
# The extras a deployment gets. `search` because DuckDuckGo is the default web
# search provider and is meant to need no setup. Listed here AND in update.sh --
# an extra added to only one of them means existing deployments silently miss it.
LEMBAS_EXTRAS="${LEMBAS_EXTRAS:-search}"
sudo -u "$SERVICE_USER" "$VENV/bin/pip" install --quiet -e "$APP[$LEMBAS_EXTRAS]"

echo "== environment =="
# Generated once and never regenerated: rotating LEMBAS_SECRET_KEY signs every
# user out AND makes the stored upstream API keys unreadable.
if [[ ! -f "$ENV_FILE" ]]; then
  KEY=$("$VENV/bin/python" -c "import secrets; print(secrets.token_urlsafe(48))")
  sudo tee "$ENV_FILE" >/dev/null <<EOF
# LLeMbas service environment. Generated by deploy/install.sh.
# LEMBAS_SECRET_KEY signs sessions and encrypts stored API keys.
# Changing it signs everyone out and makes stored API keys unreadable.
LEMBAS_SECRET_KEY=$KEY
LEMBAS_DATA_DIR=$PREFIX/data
# Loopback only: reachable through the nginx vhost, never directly.
LEMBAS_HOST=127.0.0.1
LEMBAS_PORT=$APP_PORT
LEMBAS_LOG_LEVEL=info
LEMBAS_ALLOW_SIGNUP=true
LEMBAS_DEFAULT_THEME=moria
# Which branch /admin/updates compares against. Deployment configuration, not
# an instance setting: it decides what code runs here, and a value a web
# administrator could edit would turn "you may deploy the branch" into "you may
# deploy anything".
LEMBAS_UPDATE_BRANCH=$BRANCH
# stable follows the newest release tag; edge follows the branch tip.
LEMBAS_UPDATE_CHANNEL=$CHANNEL
EOF
  sudo chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
  sudo chmod 600 "$ENV_FILE"
  echo "   generated $ENV_FILE"
else
  echo "   $ENV_FILE exists, keeping it (and its secret key)"
fi

sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 750 "$PREFIX/data"

echo "== systemd unit =="
sed -e "s|__PREFIX__|$PREFIX|g" -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
    "$HERE/lembas.service" | sudo tee /etc/systemd/system/lembas.service >/dev/null
# Which version of the template this host is running. update.sh compares
# against it and says so when the template moves on, because the installed
# unit usually grows host-specific lines and cannot simply be overwritten.
sha256sum "$HERE/lembas.service" | cut -d' ' -f1 | sudo tee "$PREFIX/.unit-applied" >/dev/null
sudo systemctl daemon-reload

echo "== update helper =="
# Two units and a marker. The marker is what the web interface reads to decide
# whether to offer the button at all -- a file rather than `systemctl
# is-enabled`, because that would be a subprocess on every page render to answer
# a question that changes once.
UPDATE_MARKER="$PREFIX/data/.update-helper"
# Where root's copy of the update script lives, and why it is a copy.
#
# The unit runs as root. Pointing its ExecStart at `$PREFIX/app/deploy/update.sh`
# meant root executing a file owned by the **unprivileged service account** --
# so anything able to write as that account could rewrite the script, create the
# request file it also owns, and be root. That is the whole privilege boundary
# the helper exists to keep, defeated by a `chown`.
#
# The second path is worse because it needs no compromise at all: an update
# pulls new code *as the service user*, and root then runs whatever
# `deploy/update.sh` that pull contained. Control of the branch would have been
# control of root.
#
# So root runs a copy it owns, installed here, by an administrator, deliberately.
# The cost is that improving `update.sh` needs `install.sh` re-run -- which is
# the correct trade: root should not execute a script that arrived over the
# network a moment ago.
UPDATE_HELPER_DIR="/usr/local/lib/lembas"
UPDATE_HELPER="$UPDATE_HELPER_DIR/update.sh"
if [[ "$INSTALL_UPDATE_HELPER" == "1" ]]; then
  sudo mkdir -p "$UPDATE_HELPER_DIR"
  sudo install -o root -g root -m 755 "$HERE/update.sh" "$UPDATE_HELPER"
  for unit in lembas-update.path lembas-update.service; do
    sed -e "s|__PREFIX__|$PREFIX|g" \
        -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
        -e "s|__UPDATE_BRANCH__|$BRANCH|g" \
        -e "s|__UPDATE_CHANNEL__|$CHANNEL|g" \
        -e "s|__UPDATE_HELPER__|$UPDATE_HELPER|g" \
        "$HERE/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
  done
  sudo systemctl daemon-reload
  sudo systemctl enable --now lembas-update.path
  # The channel goes *into* the marker, not just its existence. It is declared
  # in two places -- the unit above and lembas.env -- and this is what lets the
  # Updates page notice when somebody has edited one and not the other.
  echo "$CHANNEL" | sudo tee "$UPDATE_MARKER" >/dev/null
  sudo chown "$SERVICE_USER:$SERVICE_USER" "$UPDATE_MARKER"
  echo "   installed. The web interface can now deploy the $CHANNEL channel and restart."
else
  # Removed rather than left, so turning it off is re-running without the flag
  # rather than remembering three commands. The button then says so and prints
  # the manual one, which is the honest degradation.
  sudo systemctl disable --now lembas-update.path 2>/dev/null || true
  sudo rm -f /etc/systemd/system/lembas-update.path \
             /etc/systemd/system/lembas-update.service "$UPDATE_MARKER" \
             "$UPDATE_HELPER"
  sudo systemctl daemon-reload
  echo "   not installed (INSTALL_UPDATE_HELPER=1 to allow updating from the web UI)"
fi

echo "== self-signed cert for $SITE_HOST =="
sudo mkdir -p /etc/nginx/ssl
if [[ ! -f "/etc/nginx/ssl/$SITE_HOST.crt" ]]; then
  sudo openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "/etc/nginx/ssl/$SITE_HOST.key" -out "/etc/nginx/ssl/$SITE_HOST.crt" \
    -days 3650 -subj "/CN=$SITE_HOST" -addext "subjectAltName=DNS:$SITE_HOST"
  sudo chmod 600 "/etc/nginx/ssl/$SITE_HOST.key"
  sudo chmod 644 "/etc/nginx/ssl/$SITE_HOST.crt"
fi

echo "== nginx vhost =="
sed -e "s|__SITE_HOST__|$SITE_HOST|g" -e "s|__APP_PORT__|$APP_PORT|g" \
    "$HERE/nginx-vhost.conf" | sudo tee "/etc/nginx/conf.d/$SITE_HOST.conf" >/dev/null
sudo nginx -t
sudo systemctl reload nginx

# What this host was installed with, so update.sh can name the vhost it should
# be comparing against and print a command that actually runs. Without it the
# drift check below could only say "something changed somewhere".
printf 'SITE_HOST=%s\nAPP_PORT=%s\n' "$SITE_HOST" "$APP_PORT" \
  | sudo tee "$PREFIX/.deploy-env" >/dev/null
sha256sum "$HERE/nginx-vhost.conf" | cut -d' ' -f1 \
  | sudo tee "$PREFIX/.vhost-applied" >/dev/null

echo "== local name resolution =="
# Only useful when the LAN's DNS does not already answer for this name.
if ! getent hosts "$SITE_HOST" >/dev/null; then
  printf '127.0.0.1\t%s\n::1\t\t%s\n' "$SITE_HOST" "$SITE_HOST" | sudo tee -a /etc/hosts >/dev/null
  echo "   added $SITE_HOST to /etc/hosts"
else
  echo "   $SITE_HOST already resolves"
fi

echo "== enable service =="
sudo systemctl enable --now lembas
sleep 2
sudo systemctl --no-pager --lines=0 status lembas || true

echo
echo "LLeMbas is up at https://$SITE_HOST  (self-signed cert; accept the warning)"
echo "Create the first account -- it becomes the administrator."
