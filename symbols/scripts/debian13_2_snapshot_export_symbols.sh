#!/bin/bash
set -e

KVER=$(uname -r)
SYSMAP_PATH="/boot/System.map-${KVER}"
VMLINUX_PATH="/usr/lib/debug/lib/modules/${KVER}/vmlinux"
OUT_XZ="debian_${KVER}.json.xz"

# Debian 13.2 已验证的 dbg 快照地址
DBG_DEB_URL="https://snapshot.debian.org/archive/debian/20251106T031859Z/pool/main/l/linux/linux-image-6.12.57%2Bdeb13-amd64-dbg_6.12.57-1_amd64.deb"

echo "========== [1/5] 下载 dbg 包 =========="
DBG_DEB_FILE=$(basename "${DBG_DEB_URL}")
wget -c "${DBG_DEB_URL}" --no-check-certificate -O "${DBG_DEB_FILE}"

echo "========== [2/5] 安装 dbg 包 =========="
sudo dpkg -i "${DBG_DEB_FILE}" || true
sudo apt-get install -f -y

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
