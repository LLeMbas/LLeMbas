#!/usr/bin/env bash
# Create a Debian LXC container on a Proxmox host and install LLeMbas in it.
#
# A **wrapper around what already works**, not a second install path. It makes a
# container, puts the dependencies in it, and runs `deploy/install.sh` inside --
# which is the same script, doing the same things, so a fix to the installer
# reaches this without anybody remembering. A parallel installer would be two
# things to keep correct and one of them would rot.
#
# Run this on the Proxmox host, as root:
#
#   CTID=140 SITE_HOST=chat.example ./deploy/lxc-install.sh
#
# Everything is overridable:
#
#   CTID            next free id      the container's id
#   CT_HOSTNAME     lembas            hostname inside it
#   CT_STORAGE      local-lvm         where the rootfs goes
#   CT_TEMPLATE     debian-12         template, matched against pveam list
#   CT_DISK         12                GB
#   CT_CORES        2
#   CT_MEMORY       4096              MB
#   CT_BRIDGE       vmbr0
#   CT_IP           dhcp              or 192.168.1.50/24
#   CT_GATEWAY      (unset)           required when CT_IP is static
#   REPO_URL        this checkout's origin
#   SITE_HOST       lembas.local
#
# **Unprivileged, and that is not a default to change lightly.** Nothing LLeMbas
# does needs privilege: agent chats run their commands on a linked device, some
# *other* machine, which is the whole isolation story. A privileged container would give
# up the host's protection to buy nothing.
set -euo pipefail

CT_HOSTNAME="${CT_HOSTNAME:-lembas}"
CT_STORAGE="${CT_STORAGE:-local-lvm}"
CT_TEMPLATE="${CT_TEMPLATE:-debian-12}"
CT_DISK="${CT_DISK:-12}"
CT_CORES="${CT_CORES:-2}"
CT_MEMORY="${CT_MEMORY:-4096}"
CT_BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_IP="${CT_IP:-dhcp}"
CT_GATEWAY="${CT_GATEWAY:-}"
SITE_HOST="${SITE_HOST:-lembas.local}"
BRANCH="${LEMBAS_BRANCH:-main}"

HERE="$(dirname "$(readlink -f "$0")")"
REPO_URL="${REPO_URL:-$(git -C "$HERE" remote get-url origin 2>/dev/null || true)}"

if ! command -v pct >/dev/null; then
  echo "pct not found. Run this on a Proxmox host." >&2
  exit 1
fi
if [[ -z "$REPO_URL" ]]; then
  echo "Could not determine REPO_URL. Set it explicitly." >&2
  exit 1
fi

CTID="${CTID:-$(pvesh get /cluster/nextid)}"

# The template has to be on the host before a container can be made from it.
# Matched by prefix rather than pinned to a filename, because the point release
# in it moves and a hard-coded name would break on a host that downloaded a
# different one.
echo "== template =="
template=$(pveam list local 2>/dev/null | awk -v want="$CT_TEMPLATE" '$1 ~ want {print $1}' | head -1)
if [[ -z "$template" ]]; then
  available=$(pveam available --section system | awk -v want="$CT_TEMPLATE" '$2 ~ want {print $2}' | tail -1)
  if [[ -z "$available" ]]; then
    echo "No template matching '$CT_TEMPLATE'. Try: pveam available --section system" >&2
    exit 1
  fi
  echo "   downloading $available"
  pveam download local "$available"
  template="local:vztmpl/$available"
fi
echo "   $template"

echo "== container $CTID =="
if pct status "$CTID" >/dev/null 2>&1; then
  echo "   $CTID already exists, using it"
else
  net="name=eth0,bridge=$CT_BRIDGE,ip=$CT_IP"
  [[ -n "$CT_GATEWAY" ]] && net="$net,gw=$CT_GATEWAY"
  pct create "$CTID" "$template" \
    --hostname "$CT_HOSTNAME" \
    --cores "$CT_CORES" \
    --memory "$CT_MEMORY" \
    --rootfs "$CT_STORAGE:$CT_DISK" \
    --net0 "$net" \
    --unprivileged 1 \
    --features nesting=1 \
    --onboot 1
  echo "   created"
fi

pct start "$CTID" 2>/dev/null || true
# `pct exec` returns before the container's own network is up, and the very next
# thing this does is apt-get. Waiting on DNS resolving rather than on a fixed
# sleep, because a fixed sleep is either too short on a slow host or wasted on a
# fast one.
echo "== waiting for the network =="
for _ in $(seq 1 30); do
  pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
  sleep 2
done

echo "== dependencies =="
pct exec "$CTID" -- bash -lc '
  set -e
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends \
    git python3 python3-venv python3-pip nginx openssl sudo ca-certificates
'

echo "== checkout =="
pct exec "$CTID" -- bash -lc "
  set -e
  rm -rf /tmp/lembas-src
  git clone --quiet --branch '$BRANCH' '$REPO_URL' /tmp/lembas-src
"

# The same installer this repository ships, run inside. Everything it decides --
# the service user, the prefix, the unit, the vhost, the self-signed certificate
# -- it decides there, so this script has no opinions to keep in step with it.
#
# The update helper is **on by default here**, and only here. `install.sh`
# defaults it off because it cannot know what it is installing onto: on a shared
# or long-lived host, letting anybody who can administer the web interface
# deploy as root is a decision somebody should make on purpose. A container
# created by this script thirty seconds ago is not that host -- it exists to run
# LLeMbas and nothing else, whoever ran this owns the hypervisor, and an
# appliance you cannot update without a shell is an appliance nobody updates.
#
# Set INSTALL_UPDATE_HELPER=0 to opt back out.
INSTALL_UPDATE_HELPER="${INSTALL_UPDATE_HELPER:-1}"
LEMBAS_CHANNEL="${LEMBAS_CHANNEL:-stable}"

echo "== install =="
pct exec "$CTID" -- bash -lc "
  set -e
  SITE_HOST='$SITE_HOST' LEMBAS_BRANCH='$BRANCH' REPO_URL='$REPO_URL' \
    INSTALL_UPDATE_HELPER='$INSTALL_UPDATE_HELPER' \
    LEMBAS_CHANNEL='$LEMBAS_CHANNEL' \
    bash /tmp/lembas-src/deploy/install.sh
"

address=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "LLeMbas is installed in container $CTID."
echo "  address : ${address:-unknown}"
echo "  site    : https://$SITE_HOST  (self-signed; accept the warning)"
echo
echo "Point '$SITE_HOST' at ${address:-the container} in your DNS or hosts file,"
echo "then create the first account -- it becomes the administrator."
