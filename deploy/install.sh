#!/usr/bin/env bash
# Release bootstrap. Download into a temporary directory before changing installation.
set -euo pipefail
repo=Sakuranda/BiliMusicPlayer
version=latest
target=/opt/bilimusicplayer
args=()
while (($#)); do
  case "$1" in
    --repo) repo=${2:?}; shift 2 ;;
    --version) version=${2:?}; shift 2 ;;
    --dir) target=${2:?}; shift 2 ;;
    --help|-h)
      echo 'Usage: install.sh [--repo owner/repo] [--version v1.0.0] [--dir /opt/bilimusicplayer] --domain music.example.com --navidrome-domain navi.example.com [--install-docker] [--no-https] [--image IMAGE]'
      exit 0 ;;
    *) args+=("$1"); shift ;;
  esac
done
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo 'Invalid repository'; exit 1; }
[[ "$version" == latest || "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid version'; exit 1; }
[[ "$target" == /* && "$target" != / ]] || { echo '--dir must be an absolute installation directory'; exit 1; }
for tool in curl tar sha256sum; do command -v "$tool" >/dev/null || { echo "Missing dependency: $tool"; exit 1; }; done
temp=$(mktemp -d)
trap 'rm -rf "$temp"' EXIT
base="https://github.com/$repo/releases"
if [[ "$version" == latest ]]; then base="$base/latest/download"; else base="$base/download/$version"; fi
curl --fail --location --retry 3 "$base/bilimusicplayer.tar.gz" -o "$temp/bilimusicplayer.tar.gz"
curl --fail --location --retry 3 "$base/SHA256SUMS" -o "$temp/SHA256SUMS"
(cd "$temp"; sha256sum --check --ignore-missing SHA256SUMS)
# Reject unexpected archive layouts. Releases contain one directory with no symlinks.
if tar -tzf "$temp/bilimusicplayer.tar.gz" | LC_ALL=C grep -Ev '^BiliMusicPlayer(/([A-Za-z0-9_.@+-]|[^/[:cntrl:]])+)*(/)?$' >/dev/null; then
  echo 'Invalid release archive'; exit 1
fi
if tar -tzf "$temp/bilimusicplayer.tar.gz" | grep -E '(^|/)\.\.(/|$)|(^|/)(data|backups|\.git|\.env)(/|$)' >/dev/null; then
  echo 'Release contains an unsafe path'; exit 1
fi
if tar -tvzf "$temp/bilimusicplayer.tar.gz" | grep -E '^[lh]' >/dev/null; then
  echo 'Release contains links'; exit 1
fi
tar --no-same-owner --no-same-permissions -xzf "$temp/bilimusicplayer.tar.gz" -C "$temp"
[[ -f "$temp/BiliMusicPlayer/deploy/deploy.sh" ]] || { echo 'Incomplete release'; exit 1; }
# Configure/validate before copying, so mistakes do not replace an existing installation.
if [[ -e "$target" && ! -f "$target/deploy/.env" ]]; then
  [[ -z "$(ls -A "$target")" ]] || { echo 'Target is not a BiliMusicPlayer installation'; exit 1; }
fi
if [[ -f "$target/deploy/.env" ]]; then
  check_args=(--check)
  for arg in ${args[@]+"${args[@]}"}; do [[ "$arg" != --install-docker ]] || check_args+=(--install-docker); done
  bash "$target/deploy/deploy.sh" "${check_args[@]}"
fi
mkdir -p "$target"
cp -R "$temp/BiliMusicPlayer/." "$target/"
bash "$target/deploy/deploy.sh" ${args[@]+"${args[@]}"}
