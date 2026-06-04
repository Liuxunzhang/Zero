#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYMBOL_DIR="${SYMBOL_DIR:-${ROOT_DIR}/symbols}"
SCRIPT_DIR="${ROOT_DIR}/symbols/scripts"
KERNEL="$(uname -r)"
HOST_KERNEL="$KERNEL"
DISTRO=""
PROXY_VALUE=""

usage() {
  cat <<'USAGE'
Usage: scripts/import_symbols.sh [--distro NAME] [--kernel VERSION] [--proxy URL]

Generates a Volatility 3 Linux symbol table from local kernel debug files.
The selected generator may install/download distro debug packages and dwarf2json,
then writes the generated .json/.json.xz file into ./symbols.

Supported distro names:
  ubuntu22_24
  debian13
  debian_pre13_2
  debian13_2_snapshot
  centos6_proxy
  centos7
  centos8
  centos8_proxy

Examples:
  scripts/import_symbols.sh
  scripts/import_symbols.sh --distro ubuntu22_24
  scripts/import_symbols.sh --distro debian13 --kernel 6.12.86+deb13
  scripts/import_symbols.sh --distro centos8_proxy --proxy http://127.0.0.1:7890
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --distro|--os)
      DISTRO="${2:-}"; shift 2 ;;
    --kernel)
      KERNEL="${2:-}"; shift 2 ;;
    --proxy)
      PROXY_VALUE="${2:-}"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2 ;;
  esac
done

if [ ! -d "$SCRIPT_DIR" ]; then
  echo "Missing generator directory: $SCRIPT_DIR" >&2
  exit 1
fi

detect_distro() {
  local os_id="" os_version="" os_name=""
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    os_id="${ID:-}"
    os_version="${VERSION_ID:-}"
    os_name="${NAME:-}"
  fi

  case "$os_id" in
    ubuntu)
      echo "ubuntu22_24" ;;
    debian)
      if [[ "$KERNEL" == *deb13* ]] || [[ "$os_version" == 13* ]]; then
        echo "debian13"
      else
        echo "debian_pre13_2"
      fi ;;
    centos|rhel|rocky|almalinux)
      if [[ "$os_version" == 6* ]]; then
        echo "centos6_proxy"
      elif [[ "$os_version" == 7* ]]; then
        echo "centos7"
      else
        echo "centos8"
      fi ;;
    *)
      if echo "$os_name" | grep -qi ubuntu; then
        echo "ubuntu22_24"
      elif echo "$os_name" | grep -Eqi 'centos|red hat|rocky|alma'; then
        echo "centos8"
      else
        echo ""
      fi ;;
  esac
}

select_script() {
  case "$1" in
    ubuntu22_24|ubuntu)
      echo "ubuntu22_24_export_symbols.sh" ;;
    debian13)
      echo "debian13.sh" ;;
    debian_pre13_2|debian)
      echo "debian_pre13_2_export_symbols.sh" ;;
    debian13_2_snapshot)
      echo "debian13_2_snapshot_export_symbols.sh" ;;
    centos6_proxy|centos6)
      echo "centos6_proxy_export_symbols.sh" ;;
    centos7)
      echo "centos7_export_symbols.sh" ;;
    centos8|centos|rhel|rocky|almalinux)
      echo "centos8_export_symbols.sh" ;;
    centos8_proxy)
      echo "centos8_0_proxy_export_symbols.sh" ;;
    *)
      echo "" ;;
  esac
}

DISTRO="${DISTRO:-$(detect_distro)}"
GENERATOR="$(select_script "$DISTRO")"

if [ -z "$GENERATOR" ]; then
  echo "Could not select a symbol generator for distro '${DISTRO:-unknown}'." >&2
  usage
  exit 1
fi

GENERATOR_PATH="${SCRIPT_DIR}/${GENERATOR}"
if [ ! -x "$GENERATOR_PATH" ]; then
  echo "Generator is missing or not executable: $GENERATOR_PATH" >&2
  exit 1
fi

mkdir -p "$SYMBOL_DIR"

echo "Symbol root: $SYMBOL_DIR"
echo "Generator:   $GENERATOR"
echo "Kernel:      $KERNEL"
echo "Distro:      $DISTRO"
echo "Proxy:       ${PROXY_VALUE:-none}"

if [ "$KERNEL" != "$HOST_KERNEL" ] && [ "$GENERATOR" != "debian13.sh" ]; then
  echo "Warning: $GENERATOR uses uname -r internally; --kernel is only forwarded to debian13.sh." >&2
fi

before_list="$(mktemp)"
after_list="$(mktemp)"
find "$SCRIPT_DIR" -maxdepth 1 -type f \( -name '*.json' -o -name '*.json.xz' -o -name '*.json.gz' \) -print > "$before_list"

pushd "$SCRIPT_DIR" >/dev/null
if [ -n "$PROXY_VALUE" ]; then
  export PROXY_URL="$PROXY_VALUE"
  export http_proxy="$PROXY_VALUE"
  export https_proxy="$PROXY_VALUE"
fi

if [ "$GENERATOR" = "debian13.sh" ]; then
  "./$GENERATOR" "$KERNEL"
else
  "./$GENERATOR"
fi
popd >/dev/null

find "$SCRIPT_DIR" -maxdepth 1 -type f \( -name '*.json' -o -name '*.json.xz' -o -name '*.json.gz' \) -print > "$after_list"
new_files="$(comm -13 <(sort "$before_list") <(sort "$after_list") || true)"
rm -f "$before_list" "$after_list"

if [ -z "$new_files" ]; then
  echo "No new symbol file was detected in $SCRIPT_DIR." >&2
  echo "If the generator overwrote an existing file, move it into $SYMBOL_DIR manually." >&2
  exit 1
fi

echo "Generated symbol files:"
while IFS= read -r file; do
  target="${SYMBOL_DIR}/$(basename "$file")"
  mv -f "$file" "$target"
  ls -lh "$target"
done <<< "$new_files"
