#!/bin/bash
set -e

ARCH=$(uname -m)
KVER=$(uname -r)
PROXY_URL="${PROXY_URL:-http://192.168.0.187:10808}"
export http_proxy="$PROXY_URL"
export https_proxy="$PROXY_URL"

OFFICIAL_BASE="http://debuginfo.centos.org/8/${ARCH}/Packages"
COMMON_RPM="kernel-debuginfo-common-${ARCH}-${KVER}.rpm"
CORE_RPM="kernel-debuginfo-${KVER}.rpm"
VMLINUX_PATH="/usr/lib/debug/lib/modules/${KVER}/vmlinux"
OUT_XZ="centos8_${KVER}.json.xz"

WGET_OPTS="-c --no-check-certificate -e use_proxy=on -e http_proxy=$PROXY_URL -e https_proxy=$PROXY_URL"

echo "========== [1/5] 使用代理下载调试包 =========="
echo "代理服务器: $PROXY_URL"

wget $WGET_OPTS "${OFFICIAL_BASE}/${COMMON_RPM}" || wget $WGET_OPTS "${OFFICIAL_BASE}/k/${COMMON_RPM}"
wget $WGET_OPTS "${OFFICIAL_BASE}/${CORE_RPM}" || wget $WGET_OPTS "${OFFICIAL_BASE}/k/${CORE_RPM}"

echo "========== [2/5] 安装调试依赖包 =========="
if command -v dnf &> /dev/null; then
    sudo dnf localinstall -y "${COMMON_RPM}" "${CORE_RPM}"
else
    sudo rpm -ivh "${COMMON_RPM}" "${CORE_RPM}" --nodeps --force
fi

echo "========== [3/5] 准备 dwarf2json 工具 =========="
if [ ! -x "./dwarf2json" ]; then
    wget $WGET_OPTS "https://github.com/volatilityfoundation/dwarf2json/releases/latest/download/dwarf2json-linux-amd64" -O dwarf2json
    chmod +x dwarf2json
fi

echo "========== [4/5] 提取并压缩符号表 =========="
if [ -f "${VMLINUX_PATH}" ]; then
    ./dwarf2json linux --elf "${VMLINUX_PATH}" | xz -c -9 > "${OUT_XZ}"
    echo "符号表生成成功: ${OUT_XZ}"
else
    echo "错误：未找到 vmlinux 文件，请检查安装步骤。"
    exit 1
fi

echo "========== [5/5] 完成 =========="
ls -lh "${OUT_XZ}"
