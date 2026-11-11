#!/usr/bin/env sh
# Preflight for the CDK CLI. Node 22 and 24 are supported. Node 20 still runs every target
# but the CDK CLI prints an end-of-life banner on each command. Below 20 we stop.
set -eu
if ! command -v node >/dev/null 2>&1; then
  echo "error: node not found. Install Node 22 or 24, for example: nvm install 22" >&2
  exit 1
fi
v=$(node --version)
major=$(echo "$v" | sed 's/^v//; s/\..*//')
case "$major" in
  22|24) ;;
  20) echo "WARNING: Node $v. Supported: 22 or 24. Node 20 works here, but the CDK CLI prints an end-of-life warning on every command. Switch with: nvm use 22" >&2 ;;
  *)
    if [ "$major" -lt 20 ]; then
      echo "error: Node $v is too old for this repo. Install Node 22 or 24: nvm install 22" >&2
      exit 1
    fi
    echo "WARNING: Node $v is not tested. Supported: 22 or 24." >&2 ;;
esac
