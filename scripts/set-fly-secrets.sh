#!/usr/bin/env bash
# Feeds `fly secrets import` from .env via scripts/export_fly_secrets.py (the app's own
# dotenv parser -- the same one Settings uses), so a secret value never has to pass
# through argv or get typed into a terminal. Prints only variable names, never values,
# and only after the import has actually succeeded.
#
# LOG_LEVEL and BOT_VERSION are not secrets and are not in this list: BOT_VERSION is
# set by the image's own ENV (scripts/deploy.sh bakes it in at build time), and setting
# it as a Fly secret would override that and freeze the version across every later
# deploy. LOG_LEVEL can still be set by hand with `fly secrets set LOG_LEVEL=DEBUG`.
#
# Usage:
#   ./scripts/set-fly-secrets.sh            # imports secrets into Fly
#   ./scripts/set-fly-secrets.sh --dry-run  # prints what would be set, sends nothing
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# Also validates .env (via Settings) before anything else happens.
names="$(uv run python scripts/export_fly_secrets.py --names)"

if [[ "${1:-}" == "--dry-run" ]]; then
  while IFS= read -r name; do
    echo "would set: $name"
  done <<< "$names"
  exit 0
fi

uv run python scripts/export_fly_secrets.py | fly secrets import

while IFS= read -r name; do
  echo "Setting: $name"
done <<< "$names"

count="$(wc -l <<< "$names" | tr -d ' ')"
echo "Set $count secrets."
