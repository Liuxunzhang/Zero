#!/bin/bash
set -e

ARCH=$(uname -m)
KVER=$(uname -r)
if grep -q "Stream" /etc/os-release 2>/dev/null; then
    OS_VER="8-stream"
else
    OS_VER="8"
fi

ALIYUN_BASE="https://mirrors.aliyun.com/centos-debuginfo/${OS_VER}/${ARCH}/Packages"
COMMON_RPM="kernel-debuginfo-common-${ARCH}-${KVER}.rpm"
CORE_RPM="kernel-debuginfo-${KVER}.rpm"
VMLINUX_PATH="/usr/lib/debug/lib/modules/${KVER}/vmlinux"
OUT_XZ="centos8_${KVER}.json.xz"

echo "========== [1/5] 下载调试依赖包 (CentOS ${OS_VER}) =========="
wget -c "${ALIYUN_BASE}/${COMMON_RPM}"
wget -c "${ALIYUN_BASE}/${CORE_RPM}"

echo "========== [2/5] 安装调试依赖包 =========="
rpm -q "kernel-debuginfo-common-${ARCH}-${KVER}" &>/dev/null || sudo rpm -ivh "${COMMON_RPM}"
rpm -q "kernel-debuginfo-${KVER}" &>/dev/null || sudo rpm -ivh "${CORE_RPM}"

echo "========== [3/5] 准备 dwarf2json 工具 =========="
if [ ! -x "./dwarf2json" ]; then
    wget -c "https://gh-proxy.com/https://github.com/volatilityfoundation/dwarf2json/releases/latest/download/dwarf2json-linux-amd64" -O dwarf2json
    chmod +x dwarf2json
fi

echo "========== [4/5] 提取并压缩符号表 =========="
if [ -f "${VMLINUX_PATH}" ]; then
    ./dwarf2json linux --elf "${VMLINUX_PATH}" | xz -c -9 > "${OUT_XZ}"
    echo "符号表已生成并压缩为：${OUT_XZ}"
else
    echo "致命错误：找不到 ${VMLINUX_PATH}"
    exit 1
fi

echo "========== [5/5] 完成 =========="
ls -lh "${OUT_XZ}"
