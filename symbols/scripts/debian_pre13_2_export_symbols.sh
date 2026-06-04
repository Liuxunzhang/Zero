#!/bin/bash
set -e

KVER=$(uname -r)
CODENAME=$(lsb_release -cs)
VMLINUX_PATH="/usr/lib/debug/lib/modules/${KVER}/vmlinux"
SYSMAP_PATH="/boot/System.map-${KVER}"
OUT_XZ="debian_${KVER}_$(date +%Y%m%d).json.xz"

echo "========== [1/5] 配置 Debian Debug 源 (aliyun) =========="
cat <<EOF | sudo tee /etc/apt/sources.list.d/debian-debug-aliyun.list
deb http://mirrors.aliyun.com/debian-debug/ ${CODENAME}-debug main
deb http://mirrors.aliyun.com/debian-debug/ ${CODENAME}-proposed-updates-debug main
EOF

sudo apt update

echo "========== [2/5] 准备内核调试符号包 =========="
if [ ! -f "${VMLINUX_PATH}" ]; then
    sudo apt install -y "linux-image-${KVER}-dbg" xz-utils
else
    echo "匹配的调试符号已存在，跳过安装。"
fi

echo "========== [3/5] 准备 dwarf2json 工具 =========="
if [ ! -x "./dwarf2json" ]; then
    wget -c "https://gh-proxy.com/https://github.com/volatilityfoundation/dwarf2json/releases/latest/download/dwarf2json-linux-amd64" -O dwarf2json
    chmod +x dwarf2json
fi

echo "========== [4/5] 提取并压缩符号表 =========="
if [ -f "${VMLINUX_PATH}" ] && [ -f "${SYSMAP_PATH}" ]; then
    ./dwarf2json linux --elf "${VMLINUX_PATH}" --system-map "${SYSMAP_PATH}" | xz -c -9 > "${OUT_XZ}"
    echo "符号表处理完成: ${OUT_XZ}"
else
    echo "致命错误：无法找到 vmlinux 或 System.map"
    exit 1
fi

echo "========== [5/5] 完成 =========="
ls -lh "${OUT_XZ}"
