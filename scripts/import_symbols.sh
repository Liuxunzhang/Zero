#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYMBOL_DIR="${SYMBOL_DIR:-${ROOT_DIR}/symbols}"
REPO="${SYMBOL_REPO:-Liuxunzhang/volatility3-symbols}"
BRANCH="${SYMBOL_BRANCH:-main}"
KERNEL="$(uname -r)"
OS_ID=""
PROXY=""
QUERY=""

usage() {
  cat <<'USAGE'
Usage: scripts/import_symbols.sh [--proxy URL] [--kernel VERSION] [--repo OWNER/REPO] [--branch BRANCH] [--query TEXT]

Downloads a matching Volatility 3 Linux symbol table into ./symbols.
Supported hosts: CentOS/RHEL-like, Ubuntu, Debian.

Examples:
  scripts/import_symbols.sh
  scripts/import_symbols.sh --proxy http://127.0.0.1:7890
  scripts/import_symbols.sh --kernel 6.8.0-100-generic --query ubuntu
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --proxy)
      PROXY="${2:-}"; shift 2 ;;
    --kernel)
      KERNEL="${2:-}"; shift 2 ;;
    --repo)
      REPO="${2:-}"; shift 2 ;;
    --branch)
      BRANCH="${2:-}"; shift 2 ;;
    --query)
      QUERY="${2:-}"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2 ;;
  esac
done

if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="${ID:-}"
fi

case "$OS_ID" in
  centos|rhel|rocky|almalinux|fedora) OS_HINT="centos" ;;
  ubuntu) OS_HINT="ubuntu" ;;
  debian) OS_HINT="debian" ;;
  *) OS_HINT="" ;;
esac

if [ -n "$QUERY" ]; then
  OS_HINT="$QUERY"
fi

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need_cmd curl
need_cmd grep
need_cmd sed
need_cmd sort

CURL=(curl -fsSL --retry 3)
if [ -n "$PROXY" ]; then
  CURL+=(--proxy "$PROXY")
  export http_proxy="$PROXY"
  export https_proxy="$PROXY"
fi

API_URL="https://api.github.com/repos/${REPO}/git/trees/${BRANCH}?recursive=1"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

echo "Repository: ${REPO}@${BRANCH}"
echo "Kernel:     ${KERNEL}"
echo "OS hint:    ${OS_HINT:-none}"
echo "Proxy:      ${PROXY:-none}"

TREE="$("${CURL[@]}" "$API_URL")"

MATCHES="$(printf '%s\n' "$TREE" \
  | grep -o '"path": "[^"]*"' \
  | sed 's/^"path": "//; s/"$//' \
  | grep -Ei '\.json(\.xz|\.gz)?$|\.zip$' \
  | grep -F "$KERNEL" || true)"

if [ -n "$OS_HINT" ]; then
  FILTERED="$(printf '%s\n' "$MATCHES" | grep -Ei "$OS_HINT" || true)"
  if [ -n "$FILTERED" ]; then
    MATCHES="$FILTERED"
  fi
fi

if [ -z "$MATCHES" ]; then
  echo "No matching symbol table found for kernel '${KERNEL}'." >&2
  echo "Try --query ubuntu|debian|centos or --kernel <version>." >&2
  exit 1
fi

SELECTED="$(printf '%s\n' "$MATCHES" | sort | head -n 1)"
TARGET="${SYMBOL_DIR}/${SELECTED}"

mkdir -p "$(dirname "$TARGET")"

echo "Downloading: ${SELECTED}"
"${CURL[@]}" "${RAW_BASE}/${SELECTED}" -o "$TARGET"

echo "Imported symbol table:"
ls -lh "$TARGET"
