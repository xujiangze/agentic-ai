#!/usr/bin/env bash
# 项目初始化脚本：创建目录、虚拟环境并安装依赖
set -euo pipefail  # 严格模式：遇到错误退出、未定义变量报错、管道命令失败时退出

# 项目根目录，默认为 ~/projects/xhs-auto-publisher（可通过环境变量覆盖）
PROJECT_ROOT="${PROJECT_ROOT:-${HOME}/projects/xhs-auto-publisher}"
# Python 可执行文件路径，默认为 python3（可通过环境变量覆盖）
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "[project] preparing project directories"
# 创建项目必需的目录结构
mkdir -p "${PROJECT_ROOT}"
mkdir -p "${PROJECT_ROOT}/runtime/browser-profile"    # 浏览器配置文件目录
mkdir -p "${PROJECT_ROOT}/runtime/runs"               # 运行时数据目录
mkdir -p "${PROJECT_ROOT}/runtime/lobster-notify"     # 通知插件目录

if [ ! -d "${PROJECT_ROOT}/.venv" ]; then
  echo "[project] creating virtual environment"
  # 创建 Python 虚拟环境（仅在不存在时创建）
  "${PYTHON_BIN}" -m venv "${PROJECT_ROOT}/.venv"
fi

echo "[project] installing python dependencies"
# 升级 pip 到最新版本
"${PROJECT_ROOT}/.venv/bin/pip" install --upgrade pip
# 安装项目依赖（从 requirements.txt 读取）
"${PROJECT_ROOT}/.venv/bin/pip" install -r "${PROJECT_ROOT}/requirements.txt"

echo "[project] installing playwright chromium into this project environment"
# 安装 Playwright 的 Chromium 浏览器驱动（用于自动化操作）

"${PROJECT_ROOT}/.venv/bin/python" -m playwright install chromium
# PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright "${PROJECT_ROOT}/.venv/bin/python" -m playwright install chromium

echo "[project] done"
echo "[project] root: ${PROJECT_ROOT}"  # 输出项目根目录路径
