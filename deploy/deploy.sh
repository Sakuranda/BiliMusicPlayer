#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
domain= navidrome= image= install_docker=0 no_https=0 configure_only=0 check_only=0
fail() { echo "Error: $*" >&2; exit 1; }
while (($#)); do
  case "$1" in
    --domain) domain=${2:?}; shift 2 ;;
    --navidrome-domain) navidrome=${2:?}; shift 2 ;;
    --image) image=${2:?}; shift 2 ;;
    --install-docker) install_docker=1; shift ;;
    --no-https) no_https=1; shift ;;
    --configure-only) configure_only=1; shift ;;
    --check) check_only=1; shift ;;
    --help|-h) echo 'Usage: deploy/deploy.sh --domain music.example.com --navidrome-domain navi.example.com [--install-docker] [--no-https] [--image IMAGE] [--configure-only]'; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done
if [[ -n "$image" ]]; then
  [[ "$image" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/:@-]+$ ]] || fail 'Invalid --image'
fi
valid_domain() {
  [[ "$1" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]([a-zA-Z0-9-]*[a-zA-Z0-9])?$ && ${#1} -le 253 ]]
}
if [[ ! -f deploy/.env ]]; then
  ((check_only == 0)) || fail 'No installation configuration'
  valid_domain "$domain" || fail 'Provide --domain with your hostname (no https:// or path)'
  valid_domain "$navidrome" || fail 'Provide --navidrome-domain with a second hostname'
  [[ "$domain" != "$navidrome" ]] || fail 'Web and Navidrome need different hostnames'
  command -v openssl >/dev/null || fail 'Install openssl first'
  umask 077
  cp deploy/.env.example deploy/.env
  # Hex values need no shell escaping and work in Compose env files.
  admin_pass=$(openssl rand -hex 16)
  auth_key=$(openssl rand -hex 32)
  profile=https; origin="https://$domain"
  if ((no_https)); then profile=; origin="https://$domain"; fi
  temp_env=$(mktemp deploy/.env.XXXXXX)
  awk -v d="$domain" -v n="$navidrome" -v p="$admin_pass" -v k="$auth_key" -v h="$profile" -v o="$origin" \
    '/^MUSIC_DOMAIN=/{print "MUSIC_DOMAIN=" d;next} /^NAVIDROME_DOMAIN=/{print "NAVIDROME_DOMAIN=" n;next} /^ADMIN_PASSWORD=/{print "ADMIN_PASSWORD=" p;next} /^AUTH_SECRET=/{print "AUTH_SECRET=" k;next} /^COMPOSE_PROFILES=/{print "COMPOSE_PROFILES=" h;next} /^ALLOWED_ORIGINS=/{print "ALLOWED_ORIGINS=" o;next} {print}' deploy/.env > "$temp_env"
  mv "$temp_env" deploy/.env
else
  env_value() { awk -F= -v k="$1" '$1==k {print substr($0,index($0,"=")+1)}' deploy/.env; }
  [[ -z "$domain" || "$domain" == "$(env_value MUSIC_DOMAIN)" ]] || fail 'Existing domain differs; edit deploy/.env to change it'
  [[ -z "$navidrome" || "$navidrome" == "$(env_value NAVIDROME_DOMAIN)" ]] || fail 'Existing Navidrome domain differs; edit deploy/.env to change it'
  [[ -z "$image" || "$image" == "$(env_value APP_IMAGE)" ]] || fail 'Existing image differs; edit deploy/.env to change it'
  ((no_https == 0)) || [[ -z "$(env_value COMPOSE_PROFILES)" ]] || fail 'HTTPS mode differs; edit deploy/.env to change it'
fi
chmod 600 deploy/.env
if [[ -n "$image" ]]; then
  [[ "$image" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/:@-]+$ ]] || fail 'Invalid --image'
  temp_env=$(mktemp deploy/.env.XXXXXX)
  awk -v i="$image" '/^APP_IMAGE=/{print "APP_IMAGE=" i;next} {print}' deploy/.env > "$temp_env"
  mv "$temp_env" deploy/.env
fi
# Do not source an env file as shell code.
for key in ADMIN_USERNAME ADMIN_PASSWORD AUTH_SECRET; do
  awk -F= -v k="$key" '$1==k && length(substr($0,index($0,"=")+1))>0 {found=1} END{exit !found}' deploy/.env || fail "$key must be set in deploy/.env"
done
mkdir -p data/music/MusicPlayer-Playlists data/navidrome
if ((configure_only)); then echo 'Configuration ready; credentials are in deploy/.env (0600).'; exit 0; fi
[[ "$(uname -s)" == Linux ]] || fail 'Run installation on a Linux server; use --configure-only for local validation'
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  ((install_docker)) || fail 'Docker / Compose v2 missing. Install them or pass --install-docker'
  [[ "$(id -u)" == 0 ]] || fail 'Automatic Docker installation needs root (sudo bash ...)'
  docker_installer=$(mktemp)
  curl -fsSL https://get.docker.com -o "$docker_installer"
  sh "$docker_installer"
  rm -f "$docker_installer"
fi
docker compose version >/dev/null || fail 'Docker Compose v2 is required'
docker info >/dev/null 2>&1 || fail 'Docker daemon unavailable; run as root or a Docker-authorized user'
compose=(docker compose --env-file deploy/.env)
"${compose[@]}" config --quiet
check_downloads() {
  container=$("${compose[@]}" ps --status running -q api)
  if [[ -n "$container" ]]; then
    "${compose[@]}" exec -T api python -c 'from app.db import get_conn; c=get_conn(); n=c.execute("SELECT count(*) FROM jobs WHERE status=\x27downloading\x27").fetchone()[0]; print("Active downloads:", n); raise SystemExit(1 if n else 0)' || fail 'Downloads are active or their status cannot be checked; retry later'
  fi
}
check_downloads
if ((check_only)); then exit 0; fi
if awk -F= '$1=="APP_IMAGE" && $2!="bilimusicplayer-api:local" {found=1} END{exit !found}' deploy/.env; then
  "${compose[@]}" pull api navidrome
else
  "${compose[@]}" build api
  "${compose[@]}" pull navidrome
fi
# Build can take time; check again immediately before restarting.
check_downloads
if [[ -n "$("${compose[@]}" ps --status running -q api)" ]]; then
  "${compose[@]}" exec -T api python -c 'from app.db import get_conn; import sqlite3, pathlib, datetime; p=pathlib.Path("/data/backups"); p.mkdir(exist_ok=True); out=p/("pre-upgrade-"+datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".db"); c=get_conn(); dest=sqlite3.connect(out); c.backup(dest); dest.close(); c.close(); out.chmod(0o600); print("Database backup created")'
fi
"${compose[@]}" up -d --no-build --wait --wait-timeout 180
"${compose[@]}" exec -T api python - <<'PY'
import json, urllib.request, urllib.error
base='http://127.0.0.1:8080'
assert json.load(urllib.request.urlopen(base+'/api/health'))['ok']
assert json.load(urllib.request.urlopen(base+'/api/auth/status'))['enabled'], 'Authentication is disabled'
try:
    urllib.request.urlopen(base+'/api/songs')
except urllib.error.HTTPError as e:
    assert e.code == 401
else:
    raise SystemExit('Anonymous access must be denied')
print('Health and authentication verified')
PY
"${compose[@]}" ps
if awk -F= '$1=="COMPOSE_PROFILES" && $2=="https" {found=1} END{exit !found}' deploy/.env; then
  host=$(awk -F= '$1=="MUSIC_DOMAIN" {print $2}' deploy/.env)
  navi=$(awk -F= '$1=="NAVIDROME_DOMAIN" {print $2}' deploy/.env)
  echo "Web: https://$host  |  Navidrome: https://$navi"
  # Report certificate/DNS readiness separately from container readiness.
  curl --fail --silent --show-error --retry 5 --retry-delay 5 --retry-all-errors --connect-timeout 5 --max-time 10 "https://$host/api/health" || fail 'Containers started, but public HTTPS is not ready. Check DNS, ports 80/443 and Caddy logs; rerun deploy/deploy.sh afterwards.'
else
  echo 'Containers started on loopback only. Configure your existing HTTPS reverse proxy to API_PORT / NAVIDROME_PORT.'
fi
echo "Initial credentials: $PWD/deploy/.env (do not share). Account/optional 2FA: Web → Settings. Create a separate Navidrome account on first visit."
