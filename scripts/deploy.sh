#!/usr/bin/env bash
set -euo pipefail

[[ -z "$(git status --porcelain)" ]] || { echo "Working tree is dirty. Commit or stash before deploying." >&2; exit 1; }
fly deploy --build-arg BOT_VERSION="$(git rev-parse --short HEAD)" --ha=false
