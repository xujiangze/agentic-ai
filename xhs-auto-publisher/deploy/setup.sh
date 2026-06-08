# 设置脚本在执行时遇到错误立即退出
# -e: 遇到错误退出
# -u: 使用未定义变量时报错
# -o pipefail: 管道中任一命令失败则整体失败
#!/usr/bin/env bash
set -euo pipefail

# 定义仓库地址和项目路径常量
REPO_URL="https://github.com/DjangoPeng/agentic-ai"  # 主仓库地址
REPO_DIR="${HOME}/projects/agentic-ai"                 # 克隆后的仓库目录
PROJECT_ROOT="${HOME}/projects/xhs-auto-publisher"    # 项目符号链接目标位置

echo "[setup] step 1/3 - clone or update repo"
# 第一步：克隆或更新主仓库
# 如果本地已有仓库，则拉取最新代码；否则克隆新仓库
if [ -d "${REPO_DIR}/.git" ]; then
  git -C "${REPO_DIR}" pull        # 更新现有仓库
else
  mkdir -p "${HOME}/projects"      # 创建父目录
  git clone "${REPO_URL}" "${REPO_DIR}"  # 克隆仓库到本地
fi

echo "[setup] step 2/3 - link xhs-auto-publisher"
# 第二步：创建或更新项目符号链接
# 如果 PROJECT_ROOT 是普通目录而非链接，则删除并替换为符号链接
if [ -d "${PROJECT_ROOT}" ] && [ ! -L "${PROJECT_ROOT}" ]; then
  echo "[setup] removing old copy, replacing with symlink"
  rm -rf "${PROJECT_ROOT}"  # 删除旧的目录副本
fi
ln -sfn "${REPO_DIR}/xhs-auto-publisher" "${PROJECT_ROOT}"  # 创建符号链接到项目目录

echo "[setup] writing .env (MODE=draft)"
# 第三步：配置环境变量文件
# 从示例文件复制 .env 配置，并设置 MODE 为 draft（草稿模式）
cp "${PROJECT_ROOT}/deploy/env.example" "${PROJECT_ROOT}/.env"
sed -i 's/^MODE=.*/MODE=draft/' "${PROJECT_ROOT}/.env"  # 将 MODE 修改为 draft

echo "[setup] step 3/3 - install system dependencies and init project"
# 第四步：安装系统依赖和初始化项目
# 执行系统依赖安装脚本和项目初始化脚本
bash "${PROJECT_ROOT}/deploy/install_system_ubuntu.sh"     # 安装 Ubuntu 系统依赖
bash "${PROJECT_ROOT}/deploy/bootstrap_project.sh"         # 初始化项目配置

echo "[setup] done - project ready at ${PROJECT_ROOT}"
# 第五步：输出完成信息和后续操作指引
echo "[setup] code lives in ${REPO_DIR}/xhs-auto-publisher"      # 代码实际位置
echo "[setup] to update: git -C ${REPO_DIR} pull"               # 更新代码的命令
echo "[setup] run with: bash ${PROJECT_ROOT}/deploy/run_with_xvfb.sh"  # 运行项目的命令
