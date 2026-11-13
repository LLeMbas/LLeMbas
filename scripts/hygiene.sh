#!/usr/bin/env bash
# The repository is public: nothing in it may name the private estate it was written on.
# The rule ("everything public is current-state and estate-free"), as a check that runs
# before every push rather than a sentence somebody has to remember.
#
#   scripts/hygiene.sh        exit 0 when the tracked files are clean, 1 with the lines if not
#
# Patterns are the kinds of thing that leak: a private domain, an internal `.lan` name, a
# container number, a private address range, a machine's name. This file is the one place that
# may spell them, and is skipped.
set -euo pipefail
cd "$(dirname "$0")/.."

patterns=(
  'houmeres'
  '\b[a-z0-9-]+\.lan\b'
  '\bCT ?2[0-9]{2}\b'
  '\b10\.0\.0\.[0-9]+'
  '\b192\.168\.[0-9]+\.[0-9]+'
  '\bgamebox\b'
  '\bthor\b'
)

# Not ours to edit, or allowed to say one thing, each with its reason. A line is excused only
# when its file AND its text match an entry: the rest of the file is still checked.
#   - browser libraries are committed as downloaded (hash-pinned); a word in one is not ours;
#   - a server that refuses requests to private addresses has to name one of each range in the
#     test that proves it, and the installer shows a static address as an example.
skip='^(scripts/hygiene\.sh|src/lembas/web/static/vendor/.*)$'
excused=(
  '^tests/test_fetch\.py:[0-9]+:.*"http://192\.168\.1\.1/".*# a router'
  '^deploy/lxc-install\.sh:[0-9]+:#   CT_IP .* or 192\.168\.1\.50/24$'
)

found=0
for pattern in "${patterns[@]}"; do
  hits=$(git ls-files -z | grep -zEv -- "$skip" | xargs -0 grep -nIEi -- "$pattern" || true)
  for excuse in "${excused[@]}"; do
    hits=$(printf '%s\n' "$hits" | grep -Ev -- "$excuse" || true)
  done
  if [ -n "$hits" ]; then
    printf '%s\n' "$hits" >&2
    echo "hygiene: the lines above match /$pattern/" >&2
    found=1
  fi
done
[ "$found" -eq 0 ] && echo "hygiene: nothing private in the tracked files"
exit "$found"
