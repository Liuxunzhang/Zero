#!/bin/bash
set -e

ARCH=$(uname -m)
KVER=$(uname -r)
PROXY_URL="${PROXY_URL:-http://192.168.0.187:10808}"
export http_proxy="$PROXY_URL"
export https_proxy="$PROXY_URL"

BASE_URL="http://debuginfo.centos.org/6/${ARCH}"
COMMON_RPM="kernel-debuginfo-common-${ARCH}-${KVER}.rpm"
CORE_RPM="kernel-debuginfo-${KVER}.rpm"
VMLINUX_PATH="/usr/lib/debug/lib/modules/${KVER}/vmlinux"
OUT_XZ="centos6_${KVER}.json.xz"

echo "========== [1/5] 使用代理下载调试包 =========="
echo "使用代理: $PROXY_URL"

WGET_CMD="wget -c --no-check-certificate -e use_proxy=on -e http_proxy=$PROXY_URL -e https_proxy=$PROXY_URL"
$WGET_CMD "${BASE_URL}/${COMMON_RPM}"
$WGET_CMD "${BASE_URL}/${CORE_RPM}"

echo "========== [2/5] 安装调试依赖包 =========="
rpm -ivh "${COMMON_RPM}" --nodeps --force
rpm -ivh "${CORE_RPM}" --nodeps --force

echo "========== [3/5] 准备 dwarf2json 工具 =========="
if [ ! -x "./dwarf2json" ]; then
    $WGET_CMD "https://github.com/volatilityfoundation/dwarf2json/releases/latest/download/dwarf2json-linux-amd64" -O dwarf2json
    chmod +x dwarf2json
fi

echo "========== [4/5] 提取并压缩符号表 =========="
if [ -f "${VMLINUX_PATH}" ]; then
    ./dwarf2json linux --elf "${VMLINUX_PATH}" | xz -c -9 > "${OUT_XZ}"
    echo "符号表生成成功: ${OUT_XZ}"
else
    echo "错误: 找不到 vmlinux 文件，请确认 RPM 是否正确安装"
    exit 1
fi

echo "========== [5/5] 完成 =========="
ls -lh "${OUT_XZ}"
