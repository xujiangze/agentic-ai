# 小红书自动发布器 - 虚拟显示运行脚本
# 用途：在无界面的服务器环境中运行浏览器自动化脚本
#!/usr/bin/env bash
set -euo pipefail  # 启用严格错误检查：遇错退出、未定义变量报错、管道失败时退出

# 配置项目路径和变量（支持环境变量覆盖）
PROJECT_ROOT="${PROJECT_ROOT:-${HOME}/projects/xhs-auto-publisher}"  # 项目根目录
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"  # Python 虚拟环境路径
CONTENT_PATH="${1:-${PROJECT_ROOT}/examples/openclaw_business_content.json}"  # 内容文件路径（参数1或默认值）
MODE="${MODE:-publish}"  # 运行模式：publish(发布)或 preview(预览)
LOGIN_TIMEOUT="${LOGIN_TIMEOUT:-300}"  # 登录超时时间（秒）
DISPLAY_NUM="${DISPLAY_NUM:-99}"  # 虚拟显示编号

if [ -f "${PROJECT_ROOT}/.env" ]; then
  # 加载环境变量文件（如果存在）
  # shellcheck disable=SC1091  # 禁用 shellcheck 对动态源文件路径的检查
  set -a  # 自动导出所有变量
  . "${PROJECT_ROOT}/.env"  # 加载 .env 文件中的环境变量
  set +a  # 恢复默认变量导出行为
fi

# 打印运行参数（用于调试和日志记录）
echo "[run] project root: ${PROJECT_ROOT}"
echo "[run] content path: ${CONTENT_PATH}"
echo "[run] mode: ${MODE}"
echo "[run] login timeout: ${LOGIN_TIMEOUT}"

# 前置检查：验证必要文件和依赖
if [ ! -x "${PYTHON_BIN}" ]; then
  # 检查 Python 虚拟环境是否存在且可执行
  echo "[run] python environment not found: ${PYTHON_BIN}" >&2
  exit 1
fi

if [ ! -f "${CONTENT_PATH}" ]; then
  # 检查内容文件是否存在
  echo "[run] content file not found: ${CONTENT_PATH}" >&2
  exit 1
fi

cd "${PROJECT_ROOT}"  # 切换到项目根目录

# 使用 xvfb-run 启动虚拟 X Server（在无显示环境中运行 GUI 程序）
# xvfb (X Virtual Frame Buffer) 提供虚拟显示，支持浏览器自动化
exec xvfb-run \
  --auto-servernum \  # 自动选择可用的服务器编号
  --server-num="${DISPLAY_NUM}" \  # 指定虚拟显示编号
  --server-args="-screen 0 1440x1000x24" \  # 虚拟屏幕分辨率：1440x1000，24位色深
  "${PYTHON_BIN}" \  # Python 解释器路径
  "${PROJECT_ROOT}/scripts/publish_xhs.py" \  # 主发布脚本
  --content "${CONTENT_PATH}" \  # 内容文件路径参数
  --mode "${MODE}" \  # 运行模式参数
  --login-timeout "${LOGIN_TIMEOUT}"  # 登录超时参数
