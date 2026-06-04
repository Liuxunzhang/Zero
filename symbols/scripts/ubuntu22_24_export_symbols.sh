#!/bin/bash
set -e

KVER=$(uname -r)
CODENAME=$(lsb_release -cs)
VMLINUX_PATH="/usr/lib/debug/boot/vmlinux-${KVER}"
SYSMAP_PATH="/boot/System.map-${KVER}"
OUT_XZ="ubuntu_${KVER}.json.xz"

echo "========== [1/6] 配置 Ubuntu Debug 软件源 =========="
if ! dpkg -l | grep -q ubuntu-dbgsym-keyring; then
    sudo apt update
    sudo apt install -y ubuntu-dbgsym-keyring
fi

if ! grep -q "ddebs.ubuntu.com" /etc/apt/sources.list.d/ddebs.list 2>/dev/null; then
    echo "deb http://ddebs.ubuntu.com ${CODENAME} main restricted universe multiverse" | sudo tee /etc/apt/sources.list.d/ddebs.list
    echo "deb http://ddebs.ubuntu.com ${CODENAME}-updates main restricted universe multiverse" | sudo tee -a /etc/apt/sources.list.d/ddebs.list
    sudo apt update
fi

echo "========== [2/6] 安装内核调试符号 =========="
if ! dpkg -l | grep -q "linux-image-${KVER}-dbgsym"; then
    sudo apt install -y "linux-image-${KVER}-dbgsym" xz-utils
else
    echo "调试包 linux-image-${KVER}-dbgsym 已存在，跳过安装。"
fi

echo "========== [3/6] 准备 dwarf2json 工具 =========="
if [ ! -x "./dwarf2json" ]; then
    wget -c "https://gh-proxy.com/https://github.com/volatilityfoundation/dwarf2json/releases/latest/download/dwarf2json-linux-amd64" -O dwarf2json
    chmod +x dwarf2json
fi

echo "========== [4/6] 提取并压缩符号表 =========="
if [ -f "${VMLINUX_PATH}" ] && [ -f "${SYSMAP_PATH}" ]; then
    ./dwarf2json linux --elf "${VMLINUX_PATH}" --system-map "${SYSMAP_PATH}" | xz -c -9 > "${OUT_XZ}"
    echo "符号表已生成并压缩为：${OUT_XZ}"
else
    echo "致命错误：找不到 ${VMLINUX_PATH} 或 ${SYSMAP_PATH}"
    exit 1
fi

echo "========== [5/6] 完成 =========="
ls -lh "${OUT_XZ}"
