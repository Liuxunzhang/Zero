#!/bin/bash
# Debian 内核 ISF 符号表一键生成工具
# 用法: ./generate_debian_isf.sh [内核版本]
# 示例: ./generate_debian_isf.sh               # 生成当前内核的 ISF
#       ./generate_debian_isf.sh 6.12.86+deb13  # 生成指定内核的 ISF

set -e

# ---- 配置 ----
KERNEL="${1:-$(uname -r)}"
ARCH="amd64"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DWARF2JSON="$SCRIPT_DIR/dwarf2json"
OUTPUT="$SCRIPT_DIR/debian-${KERNEL}-${ARCH}.json"

# 多线程下载工具优先 axel，其次 aria2c，最后 wget
if command -v axel &>/dev/null; then
    DOWNLOADER="axel"
elif command -v aria2c &>/dev/null; then
    DOWNLOADER="aria2c"
else
    DOWNLOADER="wget"
fi

# 代理前缀（留空则不使用代理，填 "ptun run" 或 "proxychains" 等）
PROXY="${PROXY:-}"

echo "==> 目标内核: $KERNEL 架构: $ARCH"
echo "==> 下载工具: $DOWNLOADER  代理: ${PROXY:-无}"

# ---- Step 1: 检查 dwarf2json ----
if [ ! -x "$DWARF2JSON" ]; then
    echo "==> 下载 dwarf2json ..."
    if [ -n "$PROXY" ]; then
        $PROXY wget -q "https://github.com/volatilityfoundation/dwarf2json/releases/download/v0.9.0/dwarf2json-linux-amd64" -O "$DWARF2JSON"
    else
        wget -q "https://github.com/volatilityfoundation/dwarf2json/releases/download/v0.9.0/dwarf2json-linux-amd64" -O "$DWARF2JSON"
    fi
    chmod +x "$DWARF2JSON"
    echo "  => dwarf2json 就绪"
fi

# ---- Step 2: 检查 vmlinux 和 System.map ----
VMLINUX="/usr/lib/debug/boot/vmlinux-${KERNEL}-${ARCH}"
SYSTEM_MAP="/boot/System.map-${KERNEL}-${ARCH}"

if [ -f "$VMLINUX" ] && [ -f "$SYSTEM_MAP" ]; then
    echo "==> 调试文件已存在，跳过下载"
else
    echo "==> 需要安装调试符号包"

    # 先尝试 apt 直接安装
    DBG_PKG="linux-image-${KERNEL}-${ARCH}-dbg"
    echo "==> 尝试 apt 安装: $DBG_PKG"

    if apt-cache show "$DBG_PKG" &>/dev/null; then
        sudo apt install "$DBG_PKG" -y
    else
        # apt 没有，从 snapshot.debian.org 下载
        echo "==> apt 仓库中无此包，从 snapshot.debian.org 下载 ..."

        # 查包信息
        INFO_URL="https://snapshot.debian.org/mr/binary/${DBG_PKG}/"
        INFO=$(curl -s "$INFO_URL" 2>/dev/null || true)

        # 提取版本号
        VERSION=$(echo "$INFO" | grep -oP '"version"\s*:\s*"[^"]+"' | head -1 | grep -oP '(?<="version":")[^"]+')
        if [ -z "$VERSION" ]; then
            echo "  => 错误: 在 snapshot.debian.org 找不到 $DBG_PKG"
            echo "  => 请确保内核版本拼写正确，或手动下载"
            exit 1
        fi
        echo "  => 找到版本: $VERSION"

        # 查文件 hash
        BIN_URL="https://snapshot.debian.org/mr/binary/${DBG_PKG}/${VERSION}/binfiles"
        HASH=$(curl -s "$BIN_URL" 2>/dev/null | grep -oP '"hash"\s*:\s*"[a-f0-9]+"' | head -1 | grep -oP '(?<="hash":")[^"]+')
        if [ -z "$HASH" ]; then
            echo "  => 错误: 无法获取文件 hash"
            exit 1
        fi
        echo "  => 文件 hash: $HASH"

        # 查文件名
        FILE_INFO_URL="https://snapshot.debian.org/mr/file/${HASH}/info"
        FILE_NAME=$(curl -s "$FILE_INFO_URL" 2>/dev/null | grep -oP '"name"\s*:\s*"[^"]+"' | head -1 | grep -oP '(?<="name":")[^"]+')
        if [ -z "$FILE_NAME" ]; then
            echo "  => 错误: 无法获取文件名"
            exit 1
        fi
        echo "  => 文件名: $FILE_NAME"

        # 下载
        DEB_PATH="/tmp/${FILE_NAME}"
        DOWNLOAD_URL="https://snapshot.debian.org/file/${HASH}"

        echo "==> 开始下载 (${DOWNLOADER})..."
        case "$DOWNLOADER" in
            axel)
                if [ -n "$PROXY" ]; then
                    $PROXY axel -n 16 "$DOWNLOAD_URL" -o "$DEB_PATH"
                else
                    axel -n 16 "$DOWNLOAD_URL" -o "$DEB_PATH"
                fi
                ;;
            aria2c)
                if [ -n "$PROXY" ]; then
                    $PROXY aria2c -x 16 -s 16 "$DOWNLOAD_URL" -o "$DEB_PATH"
                else
                    aria2c -x 16 -s 16 "$DOWNLOAD_URL" -o "$DEB_PATH"
                fi
                ;;
            wget)
                if [ -n "$PROXY" ]; then
                    $PROXY wget -c "$DOWNLOAD_URL" -O "$DEB_PATH" --no-check-certificate
                else
                    wget -c "$DOWNLOAD_URL" -O "$DEB_PATH"
                fi
                ;;
        esac

        echo "==> 安装调试包 ..."
        sudo dpkg -i "$DEB_PATH"
        rm -f "$DEB_PATH"
    fi
fi

# ---- Step 3: 生成 ISF ----
echo "==> 生成 ISF 符号表 ..."
"$DWARF2JSON" linux --elf "$VMLINUX" --system-map "$SYSTEM_MAP" > "$OUTPUT"

if [ -s "$OUTPUT" ]; then
    SIZE=$(du -h "$OUTPUT" | cut -f1)
    echo ""
    echo "============================================"
    echo "  ✔ 生成成功！"
    echo "  文件: $OUTPUT"
    echo "  大小: $SIZE"
    echo "  用法: cp $OUTPUT volatility3/symbols/linux/"
    echo "============================================"
else
    echo "✘ 生成失败，输出文件为空"
    exit 1
fi
