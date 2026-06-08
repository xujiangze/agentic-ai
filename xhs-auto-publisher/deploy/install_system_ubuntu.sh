#!/usr/bin/env bash
# 设置严格的错误处理：遇到错误立即退出、未定义变量报错、管道命令失败时退出
set -euo pipefail

echo "[system] installing ubuntu packages required by browser automation"
# 设置为非交互模式，避免 apt 安装过程中的用户提示
export DEBIAN_FRONTEND=noninteractive
apt-get update
# 安装浏览器自动化所需的系统依赖
apt-get install -y \
  python3 \              # Python 3 解释器
  python3-pip \          # Python 3 包管理工具
  python3-venv \         # Python 3 虚拟环境支持
  xvfb \                 # 虚拟 X 服务器，用于无头环境运行浏览器
  curl \                 # HTTP 客户端工具
  unzip \                # 解压缩工具
  libnss3 \              # 网络安全服务库（用于 SSL/TLS）
  libatk-bridge2.0-0 \   # 辅助功能桥接库
  libxkbcommon0 \        # 键盘处理库
  libgtk-3-0 \           # GTK+ 3.0 图形界面库
  libgbm1 \              # 图形缓冲管理器
  libasound2t64 \        # ALSA 音频库
  libxshmfence1 \        # 共享内存栅栏库
  libxcomposite1 \       # X11 组合扩展库
  libxdamage1 \          # X11 损坏扩展库
  libxfixes3 \           # X11 修复扩展库
  libxrandr2 \           # X11 渲染扩展库
  libdrm2 \              # 直接渲染管理器库
  libatk1.0-0 \          # 辅助功能工具包
  libcups2 \             # 打印服务库
  libdbus-1-3 \          # D-Bus 消息总线库
  libnspr4               # 网络安全运行时库

echo "[system] done"
