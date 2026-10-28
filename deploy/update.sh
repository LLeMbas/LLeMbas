#!/usr/bin/env bash
# Pull the latest LLeMbas into the deployment and restart the service.
#
# Run this after pushing. It fetches, hard-resets the deployment checkout to the
# remote branch, reinstalls dependencies if they changed, and restarts. Nothing
# is ever edited in place under the deployment prefix, so a hard reset is safe
# and avoids merge conflicts from a dirty tree.
set -euo pipefail

SERVICE_USER="${SERVICE_USER:-lembas}"
PREFIX="${PREFIX:-/srv/lembas}"
BRANCH="${LEMBAS_BRANCH:-main}"
# `stable` deploys the newest release tag; `edge` deploys the branch tip. Stable
# is the default because a branch tip is not a release -- following one means
# deploying whatever was pushed five minutes ago. A host with no tags yet falls
# back to the branch and says so, rather than refusing to update at all.
CHANNEL="${LEMBAS_CHANNEL:-stable}"

APP="$PREFIX/app"
VENV="$PREFIX/venv"

if [[ ! -d "$APP/.git" ]]; then
  echo "No deployment at $APP. Run deploy/install.sh first." >&2
  exit 1
fi

git_as() { sudo -u "$SERVICE_USER" git -C "$APP" "$@"; }

before=$(git_as rev-parse HEAD)

echo "== fetching =="
# `--tags` and `--force`: without the first, the stable channel never learns
# about a release; without the second, a tag that was moved -- which happens to a
# release cut wrong -- is refused rather than updated, and the host sits on the
# old one with no sign of why.
git_as fetch --quiet --tags --force origin "$BRANCH"

# What to land on. A release tag on stable, the branch tip on edge. The tag
# pattern deliberately excludes anything with a suffix: `v1.1.0-rc1` sorts above
# `v1.1.0` under git's version sort, so accepting it would step a stable host
# onto a release candidate on the strength of a hyphen.
target="origin/$BRANCH"
if [[ "$CHANNEL" == "stable" ]]; then
  # `|| true` is load-bearing under `set -euo pipefail`, and for two reasons:
  # grep exits 1 when nothing matches -- which is every host until the first
  # release is tagged -- and `head -1` closing the pipe early can hand grep a
  # SIGPIPE. Either kills the script mid-update, after the fetch and before the
  # reset, leaving the checkout fetched and unmoved with no error printed.
  newest=$(git_as tag --list --sort=-v:refname \
             | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true)
  if [[ -n "$newest" ]]; then
    target="$newest"
  else
    echo "   no release tags yet; following $BRANCH instead"
  fi
fi
echo "   channel $CHANNEL -> $target"

if [[ "$target" == "origin/$BRANCH" ]]; then
  # Stays on the branch, which is what this always did.
  git_as reset --hard --quiet "$target"
else
  # Detached at the tag. A `reset --hard <tag>` while on `main` would move the
  # local branch to it, which is a rewrite of a ref nobody asked to rewrite --
  # and the deployment checkout is never developed in, so being at a commit
  # rather than on a branch is the more honest state anyway.
  git_as -c advice.detachedHead=false checkout --force --detach --quiet "$target"
fi

after=$(git_as rev-parse HEAD)

if [[ "$before" == "$after" ]]; then
  echo "   already at ${after:0:7}, nothing to pull"
else
  echo "   ${before:0:7} -> ${after:0:7}"
  git_as --no-pager log --oneline "$before..$after" | sed 's/^/     /'
fi

# Cheap and idempotent; catches a dependency added since the last deploy.
# The extras a deployment gets. `search` because DuckDuckGo is the default web
# search provider and is meant to need no setup; `backup` for zstd backups. Listed here AND in install.sh --
# an extra added to only one of them means existing deployments silently miss it.
LEMBAS_EXTRAS="${LEMBAS_EXTRAS:-search,backup}"
echo "== dependencies =="
sudo -u "$SERVICE_USER" "$VENV/bin/pip" install --quiet -e "$APP[$LEMBAS_EXTRAS]"

# The unit is NOT reinstalled automatically. An installed unit usually carries
# host-specific lines the template cannot know about -- an ordering dependency
# on whatever serves the models, a note about how the prefix is mounted -- and
# overwriting those on every update would be a worse surprise than drifting.
#
# So this compares the *template* against the one last applied here, not the
# template against the installed file. Comparing the files would warn forever
# about the local lines, and a warning that always fires is one nobody reads.
#
# The drift is worth catching: a change in the unit can be what makes a release
# work at all, and a host that pulled the code without it would run the new
# version under the old settings and fail confusingly.
# This script itself, first, because root is running a copy of it.
#
# `install.sh` puts a root-owned copy outside the checkout and points the unit
# there -- root must not execute a file the unprivileged service account can
# write, nor one that an update just fetched. The cost of that is exactly this:
# the copy can fall behind what the checkout ships, silently, and the way to
# notice is to compare.
#
# `$0` is the copy being run; `$APP/deploy/update.sh` is what was just pulled.
self=$(readlink -f "$0")
if [[ "$self" == "$(readlink -f "$APP")"/* ]]; then
  # The unit points *into the checkout*, so root is executing a file the
  # unprivileged service account owns and that every update overwrites.
  echo "== update helper: INSECURE WIRING ==" >&2
  echo "   This unit runs $self as root, and that file is owned by" >&2
  echo "   $SERVICE_USER -- the account the web application runs as. Anything" >&2
  echo "   able to write as that account can rewrite it and be root, and so" >&2
  echo "   can whoever controls the branch this host follows." >&2
  echo "   Fix by re-running the installer, which moves root's copy out:" >&2
  echo "     cd $APP && sudo INSTALL_UPDATE_HELPER=1 ./deploy/install.sh" >&2
elif [[ -f "$APP/deploy/update.sh" ]]; then
  running_helper=$(sha256sum "$self" | cut -d' ' -f1)
  shipped_helper=$(sha256sum "$APP/deploy/update.sh" | cut -d' ' -f1)
  if [[ "$running_helper" != "$shipped_helper" ]]; then
    echo "== update helper ==" >&2
    echo "   deploy/update.sh has changed since this host's copy was installed." >&2
    echo "   Re-run the installer to take it:" >&2
    echo "     cd $APP && sudo INSTALL_UPDATE_HELPER=1 ./deploy/install.sh" >&2
  fi
fi

STAMP="$PREFIX/.unit-applied"
current=$(sha256sum "$APP/deploy/lembas.service" | cut -d' ' -f1)
if [[ -f "$STAMP" && "$(cat "$STAMP")" != "$current" ]]; then
  echo "== systemd unit ==" >&2
  echo "   deploy/lembas.service has changed since it was last applied here." >&2
  echo "   Review it and merge by hand, keeping this host's own lines:" >&2
  echo "     diff /etc/systemd/system/lembas.service <(sed \\" >&2
  echo "       -e 's|__PREFIX__|$PREFIX|g' -e 's|__SERVICE_USER__|$SERVICE_USER|g' \\" >&2
  echo "       $APP/deploy/lembas.service)" >&2
  echo "   Then: sudo systemctl daemon-reload && sudo systemctl restart lembas" >&2
  echo "   And record it as applied: echo $current | sudo tee $STAMP" >&2
elif [[ ! -f "$STAMP" ]]; then
  # First run after this check was added. Assume what is installed is current;
  # there is nothing to compare against and crying wolf on every host once is
  # not worth it.
  echo "$current" | sudo tee "$STAMP" >/dev/null
fi

# The same argument for the vhost, and the failure is worse. A stale unit at
# least says something in the journal; a stale vhost breaks a feature two layers
# away, and the only symptom is a panel that says it could not connect. The
# terminal is a WebSocket, and a `location` that does not pass an upgrade
# through fails every handshake while every test in the suite still passes.
VHOST_STAMP="$PREFIX/.vhost-applied"
vhost_now=$(sha256sum "$APP/deploy/nginx-vhost.conf" | cut -d' ' -f1)

site_host=""; app_port=""
# Written by install.sh. Where it is missing, the port is recovered from the
# environment file and the vhost found by what it proxies to, so the one-time
# check below still runs.
# **Parsed, never sourced.** `.deploy-env` is written by the installer with
# `sudo tee`, so the file is root-owned -- but `$PREFIX` is the service
# account's own directory, mode 755, and write permission on a directory is all
# it takes to unlink a file and put another one there. `.` would have run its
# contents as root, and this script is root-triggerable by anyone who can create
# one file in `$PREFIX/data` -- which is that same account. Two keys, two
# patterns, and anything else in the file is ignored rather than executed.
if [[ -f "$PREFIX/.deploy-env" ]]; then
  site_host=$(sed -n 's/^SITE_HOST=\([A-Za-z0-9._-]\{1,253\}\)$/\1/p' \
                "$PREFIX/.deploy-env" | tail -1)
  app_port=$(sed -n 's/^APP_PORT=\([0-9]\{1,5\}\)$/\1/p' \
               "$PREFIX/.deploy-env" | tail -1)
fi
if [[ -z "$app_port" && -f "$PREFIX/lembas.env" ]]; then
  app_port=$(sed -n 's/^LEMBAS_PORT=//p' "$PREFIX/lembas.env" | tail -1)
fi
app_port="${app_port:-8080}"

installed_vhost=""
if [[ -n "$site_host" && -f "/etc/nginx/conf.d/$site_host.conf" ]]; then
  installed_vhost="/etc/nginx/conf.d/$site_host.conf"
else
  installed_vhost=$(grep -ls "proxy_pass http://127.0.0.1:$app_port" \
                      /etc/nginx/conf.d/*.conf 2>/dev/null | head -1)
fi

vhost_stale=""
if [[ -f "$VHOST_STAMP" ]]; then
  [[ "$(cat "$VHOST_STAMP")" != "$vhost_now" ]] && vhost_stale="the template has changed"
elif [[ -n "$installed_vhost" ]]; then
  # First run with this check, so there is no stamp to compare against. Rather
  # than assume what is installed is current -- which is what the unit check
  # does, and would hide exactly the change this was added for -- look for the
  # one thing that must be there. Everything else is left to the stamp.
  grep -q 'lembas_connection_upgrade' "$installed_vhost" \
    || vhost_stale="the installed vhost does not pass WebSocket upgrades through, so the terminal cannot connect"
fi

if [[ -n "$vhost_stale" ]]; then
  echo "== nginx vhost ==" >&2
  echo "   $vhost_stale." >&2
  echo "   Review and reinstall it:" >&2
  echo "     diff ${installed_vhost:-/etc/nginx/conf.d/your-host.conf} <(sed \\" >&2
  echo "       -e 's|__SITE_HOST__|${site_host:-your-host}|g' -e 's|__APP_PORT__|$app_port|g' \\" >&2
  echo "       $APP/deploy/nginx-vhost.conf)" >&2
  echo "   Then: sudo nginx -t && sudo systemctl reload nginx" >&2
  echo "   And record it as applied: echo $vhost_now | sudo tee $VHOST_STAMP" >&2
elif [[ ! -f "$VHOST_STAMP" ]]; then
  echo "$vhost_now" | sudo tee "$VHOST_STAMP" >/dev/null
fi

echo "== restart =="
sudo systemctl restart lembas
sleep 2

if systemctl is-active --quiet lembas; then
  echo "   lembas is running"
else
  echo "   lembas FAILED to start:" >&2
  sudo journalctl -u lembas -n 30 --no-pager >&2
  exit 1
fi
