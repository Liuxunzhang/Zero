#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYMBOL_DIR="${SYMBOL_DIR:-${ROOT_DIR}/symbols}"
WORK_DIR="${WORK_DIR:-${SYMBOL_DIR}/.build}"
KERNEL="$(uname -r)"
DISTRO=""
PROXY_URL=""
DEBIAN_SNAPSHOT_URL="${DEBIAN_SNAPSHOT_URL:-https://snapshot.debian.org/archive/debian/20251106T031859Z/pool/main/l/linux/linux-image-6.12.57%2Bdeb13-amd64-dbg_6.12.57-1_amd64.deb}"

usage() {
  cat <<'USAGE'
Usage: scripts/import_symbols.sh [--distro NAME] [--kernel VERSION] [--proxy URL] [--snapshot-url URL]

Generate a Volatility 3 Linux symbol table for the target kernel.
The script summarizes the old per-distro scripts into one entrypoint:
it installs/downloads kernel debug packages, prepares dwarf2json, and writes
the generated .json.xz file into ./symbols.

Supported distro names:
  ubuntu22_24        Ubuntu 22.04/24.04 dbgsym flow
  debian13           Debian 13 dbg package, apt first then snapshot lookup
  debian_pre13_2     Debian debug repository flow
  debian13_snapshot  Fixed Debian snapshot package URL flow
  centos6            CentOS 6 debuginfo flow, proxy-friendly
  centos7            CentOS 7 Aliyun debuginfo flow
  centos8            CentOS 8 / 8 Stream Aliyun debuginfo flow
  centos8_proxy      CentOS 8 official debuginfo flow through proxy

Examples:
  scripts/import_symbols.sh
  scripts/import_symbols.sh --distro ubuntu22_24
  scripts/import_symbols.sh --distro centos7
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
      PROXY_URL="${2:-}"; shift 2 ;;
    --snapshot-url)
      DEBIAN_SNAPSHOT_URL="${2:-}"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2 ;;
  esac
done

mkdir -p "$SYMBOL_DIR" "$WORK_DIR"

if [ -n "$PROXY_URL" ]; then
  export http_proxy="$PROXY_URL"
  export https_proxy="$PROXY_URL"
fi

log_step() {
  echo >&2
  echo "========== $* ==========" >&2
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

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
        echo "centos6"
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

normalize_distro() {
  case "$1" in
    ubuntu|ubuntu22|ubuntu24|ubuntu22_24) echo "ubuntu22_24" ;;
    debian13) echo "debian13" ;;
    debian|debian_pre13|debian_pre13_2) echo "debian_pre13_2" ;;
    debian13_snapshot|debian13_2_snapshot) echo "debian13_snapshot" ;;
    centos6|centos6_proxy) echo "centos6" ;;
    centos7) echo "centos7" ;;
    centos8|centos|rhel|rocky|almalinux) echo "centos8" ;;
    centos8_proxy|centos8_0_proxy) echo "centos8_proxy" ;;
    *) echo "" ;;
  esac
}

download_file() {
  local url="$1"
  local output="$2"
  shift 2
  if command -v wget >/dev/null 2>&1; then
    if [ -n "$PROXY_URL" ]; then
      wget -c --no-check-certificate -e use_proxy=on -e http_proxy="$PROXY_URL" -e https_proxy="$PROXY_URL" "$url" -O "$output" "$@"
    else
      wget -c --no-check-certificate "$url" -O "$output" "$@"
    fi
  elif command -v curl >/dev/null 2>&1; then
    if [ -n "$PROXY_URL" ]; then
      curl -fL --retry 3 --proxy "$PROXY_URL" "$url" -o "$output"
    else
      curl -fL --retry 3 "$url" -o "$output"
    fi
  else
    echo "Missing downloader: wget or curl" >&2
    exit 1
  fi
}

ensure_dwarf2json() {
  local dwarf="${WORK_DIR}/dwarf2json"
  if [ -x "$dwarf" ]; then
    echo "$dwarf"
    return
  fi

  log_step "准备 dwarf2json"
  local url="https://github.com/volatilityfoundation/dwarf2json/releases/latest/download/dwarf2json-linux-amd64"
  download_file "$url" "$dwarf"
  chmod +x "$dwarf"
  echo "$dwarf"
}

emit_symbol() {
  local vmlinux="$1"
  local system_map="$2"
  local output_name="$3"
  local output="${SYMBOL_DIR}/${output_name}"
  local dwarf
  dwarf="$(ensure_dwarf2json)"

  log_step "生成符号表"
  if [ ! -f "$vmlinux" ]; then
    echo "Missing vmlinux: $vmlinux" >&2
    exit 1
  fi

  if [ -n "$system_map" ]; then
    if [ ! -f "$system_map" ]; then
      echo "Missing System.map: $system_map" >&2
      exit 1
    fi
    "$dwarf" linux --elf "$vmlinux" --system-map "$system_map" | xz -c -9 > "$output"
  else
    "$dwarf" linux --elf "$vmlinux" | xz -c -9 > "$output"
  fi

  if [ ! -s "$output" ]; then
    echo "Generated symbol table is empty: $output" >&2
    exit 1
  fi
  ls -lh "$output"
}

install_ubuntu_debug() {
  need_cmd sudo
  need_cmd apt
  local codename
  codename="$(lsb_release -cs)"

  log_step "配置 Ubuntu Debug 软件源"
  if ! dpkg -l | grep -q ubuntu-dbgsym-keyring; then
    sudo apt update
    sudo apt install -y ubuntu-dbgsym-keyring
  fi

  if ! grep -q "ddebs.ubuntu.com" /etc/apt/sources.list.d/ddebs.list 2>/dev/null; then
    echo "deb http://ddebs.ubuntu.com ${codename} main restricted universe multiverse" | sudo tee /etc/apt/sources.list.d/ddebs.list
    echo "deb http://ddebs.ubuntu.com ${codename}-updates main restricted universe multiverse" | sudo tee -a /etc/apt/sources.list.d/ddebs.list
    sudo apt update
  fi

  log_step "安装 Ubuntu 内核调试符号"
  if ! dpkg -l | grep -q "linux-image-${KERNEL}-dbgsym"; then
    sudo apt install -y "linux-image-${KERNEL}-dbgsym" xz-utils
  else
    echo "linux-image-${KERNEL}-dbgsym already installed"
  fi

  emit_symbol "/usr/lib/debug/boot/vmlinux-${KERNEL}" "/boot/System.map-${KERNEL}" "ubuntu_${KERNEL}.json.xz"
}

install_debian_pre13_debug() {
  need_cmd sudo
  need_cmd apt
  local codename
  codename="$(lsb_release -cs)"

  log_step "配置 Debian Debug 源"
  cat <<EOF | sudo tee /etc/apt/sources.list.d/debian-debug-aliyun.list
deb http://mirrors.aliyun.com/debian-debug/ ${codename}-debug main
deb http://mirrors.aliyun.com/debian-debug/ ${codename}-proposed-updates-debug main
EOF
  sudo apt update

  log_step "安装 Debian 内核调试符号"
  if [ ! -f "/usr/lib/debug/lib/modules/${KERNEL}/vmlinux" ]; then
    sudo apt install -y "linux-image-${KERNEL}-dbg" xz-utils
  else
    echo "matching debug symbols already installed"
  fi

  emit_symbol "/usr/lib/debug/lib/modules/${KERNEL}/vmlinux" "/boot/System.map-${KERNEL}" "debian_${KERNEL}.json.xz"
}

install_debian13_debug() {
  need_cmd sudo
  need_cmd apt
  need_cmd curl

  local arch="amd64"
  local dbg_pkg="linux-image-${KERNEL}-${arch}-dbg"
  local vmlinux="/usr/lib/debug/boot/vmlinux-${KERNEL}-${arch}"
  local system_map="/boot/System.map-${KERNEL}-${arch}"

  log_step "准备 Debian 13 内核调试符号"
  if [ -f "$vmlinux" ] && [ -f "$system_map" ]; then
    echo "debug files already exist"
  elif apt-cache show "$dbg_pkg" >/dev/null 2>&1; then
    sudo apt install -y "$dbg_pkg" xz-utils
  else
    log_step "从 snapshot.debian.org 查找 ${dbg_pkg}"
    local info version hash file_name deb_path download_url
    info="$(curl -s "https://snapshot.debian.org/mr/binary/${dbg_pkg}/" 2>/dev/null || true)"
    version="$(echo "$info" | grep -oP '"version"\s*:\s*"[^"]+"' | head -1 | grep -oP '(?<="version":")[^"]+' || true)"
    if [ -z "$version" ]; then
      echo "Could not find snapshot package: $dbg_pkg" >&2
      exit 1
    fi
    hash="$(curl -s "https://snapshot.debian.org/mr/binary/${dbg_pkg}/${version}/binfiles" 2>/dev/null | grep -oP '"hash"\s*:\s*"[a-f0-9]+"' | head -1 | grep -oP '(?<="hash":")[^"]+' || true)"
    if [ -z "$hash" ]; then
      echo "Could not resolve snapshot file hash for: $dbg_pkg" >&2
      exit 1
    fi
    file_name="$(curl -s "https://snapshot.debian.org/mr/file/${hash}/info" 2>/dev/null | grep -oP '"name"\s*:\s*"[^"]+"' | head -1 | grep -oP '(?<="name":")[^"]+' || true)"
    if [ -z "$file_name" ]; then
      echo "Could not resolve snapshot file name for hash: $hash" >&2
      exit 1
    fi
    deb_path="${WORK_DIR}/${file_name}"
    download_url="https://snapshot.debian.org/file/${hash}"
    download_file "$download_url" "$deb_path"
    sudo dpkg -i "$deb_path" || sudo apt-get install -f -y
  fi

  emit_symbol "$vmlinux" "$system_map" "debian-${KERNEL}-${arch}.json.xz"
}

install_debian13_snapshot_debug() {
  need_cmd sudo
  local deb_file
  deb_file="${WORK_DIR}/$(basename "$DEBIAN_SNAPSHOT_URL")"

  log_step "下载 Debian 固定快照 dbg 包"
  download_file "$DEBIAN_SNAPSHOT_URL" "$deb_file"

  log_step "安装 Debian 固定快照 dbg 包"
  sudo dpkg -i "$deb_file" || sudo apt-get install -f -y

  emit_symbol "/usr/lib/debug/lib/modules/${KERNEL}/vmlinux" "/boot/System.map-${KERNEL}" "debian_${KERNEL}.json.xz"
}

install_centos_debug() {
  local flavor="$1"
  need_cmd sudo
  need_cmd rpm

  local arch common_rpm core_rpm base_url vmlinux output_name
  arch="$(uname -m)"
  common_rpm="kernel-debuginfo-common-${arch}-${KERNEL}.rpm"
  core_rpm="kernel-debuginfo-${KERNEL}.rpm"
  vmlinux="/usr/lib/debug/lib/modules/${KERNEL}/vmlinux"

  case "$flavor" in
    centos6)
      base_url="http://debuginfo.centos.org/6/${arch}"
      output_name="centos6_${KERNEL}.json.xz" ;;
    centos7)
      base_url="https://mirrors.aliyun.com/centos-debuginfo/7/${arch}"
      output_name="centos7_${KERNEL}.json.xz" ;;
    centos8)
      local os_ver="8"
      if grep -q "Stream" /etc/os-release 2>/dev/null; then
        os_ver="8-stream"
      fi
      base_url="https://mirrors.aliyun.com/centos-debuginfo/${os_ver}/${arch}/Packages"
      output_name="centos8_${KERNEL}.json.xz" ;;
    centos8_proxy)
      base_url="http://debuginfo.centos.org/8/${arch}/Packages"
      output_name="centos8_${KERNEL}.json.xz" ;;
    *)
      echo "Unsupported CentOS flavor: $flavor" >&2
      exit 1 ;;
  esac

  log_step "下载 CentOS 调试 RPM"
  local common_path="${WORK_DIR}/${common_rpm}"
  local core_path="${WORK_DIR}/${core_rpm}"
  if [ "$flavor" = "centos8_proxy" ]; then
    download_file "${base_url}/${common_rpm}" "$common_path" || download_file "${base_url}/k/${common_rpm}" "$common_path"
    download_file "${base_url}/${core_rpm}" "$core_path" || download_file "${base_url}/k/${core_rpm}" "$core_path"
  else
    download_file "${base_url}/${common_rpm}" "$common_path"
    download_file "${base_url}/${core_rpm}" "$core_path"
  fi

  log_step "安装 CentOS 调试 RPM"
  if command -v dnf >/dev/null 2>&1; then
    sudo dnf localinstall -y "$common_path" "$core_path"
  else
    rpm -q "kernel-debuginfo-common-${arch}-${KERNEL}" >/dev/null 2>&1 || sudo rpm -ivh "$common_path" --nodeps --force
    rpm -q "kernel-debuginfo-${KERNEL}" >/dev/null 2>&1 || sudo rpm -ivh "$core_path" --nodeps --force
  fi

  emit_symbol "$vmlinux" "" "$output_name"
}

DISTRO="$(normalize_distro "${DISTRO:-$(detect_distro)}")"
if [ -z "$DISTRO" ]; then
  echo "Could not detect a supported distro. Please pass --distro." >&2
  usage
  exit 1
fi

echo "Symbol root: $SYMBOL_DIR"
echo "Work dir:    $WORK_DIR"
echo "Kernel:      $KERNEL"
echo "Distro:      $DISTRO"
echo "Proxy:       ${PROXY_URL:-none}"

case "$DISTRO" in
  ubuntu22_24) install_ubuntu_debug ;;
  debian13) install_debian13_debug ;;
  debian_pre13_2) install_debian_pre13_debug ;;
  debian13_snapshot) install_debian13_snapshot_debug ;;
  centos6) install_centos_debug centos6 ;;
  centos7) install_centos_debug centos7 ;;
  centos8) install_centos_debug centos8 ;;
  centos8_proxy) install_centos_debug centos8_proxy ;;
  *)
    echo "Unsupported distro: $DISTRO" >&2
    exit 1 ;;
esac
