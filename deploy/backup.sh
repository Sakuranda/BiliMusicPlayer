#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash deploy/deploy.sh --check
compose=(docker compose --env-file deploy/.env)
umask 077
mkdir -p backups
out="backups/full-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
trap '"${compose[@]}" up -d --no-build' EXIT
"${compose[@]}" stop
tar --exclude=data/backups -czf "$out" deploy/.env VERSION data
echo "Backup: $PWD/$out (includes credentials and cookies; keep private)"
