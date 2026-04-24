# ============================================================
# OpenClaw 一键部署脚本 (Windows 10 版)
# 课程：AI 业务流架构师 · 第二节课实战
#
# 使用方法：
#   1. 以管理员身份打开 PowerShell
#   2. 如果首次运行脚本，先执行：
#      Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#   3. 运行脚本：
#      .\setup-openclaw.ps1
#
# 前置条件：
#   - Windows 10（64 位）
#   - 以管理员身份运行 PowerShell
#   - 准备好大模型 API Key（OpenAI / DeepSeek / 豆包等均可）
#
# 部署架构（6 步）：
#   Step 1  系统环境检查与依赖安装
#   Step 2  安装 Node.js 与 OpenClaw
#   Step 3  配置 API Key 并启动 Gateway（注册为 Windows 服务）
#   Step 4  安装 Tailscale
#   Step 5  配置 Tailscale Serve 与 Dashboard 访问（手动）
#   Step 6  配置防火墙（可选）
# ============================================================

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

# ---------- 辅助函数 ----------
function Write-StepHeader {
    param([string]$StepNum, [string]$Title)
    Write-Host ""
    Write-Host ("=" * 50) -ForegroundColor Cyan
    Write-Host "  [Step $StepNum] $Title" -ForegroundColor Cyan
    Write-Host ("=" * 50) -ForegroundColor Cyan
    Write-Host ""
}

function Write-StepDone {
    param([string]$Message)
    Write-Host ""
    Write-Host "  $([char]0x2705) $Message" -ForegroundColor Green
    Write-Host ""
}

function Refresh-Path {
    # 重新加载 PATH 使新安装的程序立即可用
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path    = "$machinePath;$userPath"
}

# ---------- 开场 ----------
Write-Host ""
Write-Host ("=" * 50) -ForegroundColor Yellow
Write-Host "  OpenClaw 一键部署脚本 (Windows 10)" -ForegroundColor Yellow
Write-Host "  课程：AI 业务流架构师 · 第二节课" -ForegroundColor Yellow
Write-Host ("=" * 50) -ForegroundColor Yellow
Write-Host ""

# ==========================================================
# Step 1: 系统环境检查与依赖安装
# ==========================================================
Write-StepHeader "1/6" "系统环境检查与依赖安装"

# 检查 winget 是否可用
$hasWinget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $hasWinget) {
    Write-Host "  winget 不可用。请先从 Microsoft Store 安装 'App Installer'" -ForegroundColor Red
    Write-Host "  或从 https://aka.ms/getwinget 下载安装。" -ForegroundColor Red
    exit 1
}
Write-Host "  winget 已就绪"

# 安装 Git（如未安装）
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "  安装 Git..."
    winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
    Refresh-Path
} else {
    Write-Host "  Git 已安装，跳过"
}

Write-StepDone "Step 1 完成：系统环境已就绪"

# ==========================================================
# Step 2: 安装 Node.js 与 OpenClaw
# ==========================================================
Write-StepHeader "2/6" "安装 Node.js 与 OpenClaw"

$nodeMajor = 0
if (Get-Command node -ErrorAction SilentlyContinue) {
    $nodeVer = (node -v) -replace '^v', ''
    $nodeMajor = [int]($nodeVer.Split('.')[0])
}

if ($nodeMajor -lt 24) {
    Write-Host "  安装 Node.js 24（通过 winget）..."
    # winget 中 Node.js 24 的 ID
    winget install --id OpenJS.NodeJS --version-match "24." -e --accept-source-agreements --accept-package-agreements 2>$null
    if ($LASTEXITCODE -ne 0) {
        # 如果指定版本匹配失败，安装最新 LTS
        Write-Host "  尝试安装最新版 Node.js..."
        winget install --id OpenJS.NodeJS -e --accept-source-agreements --accept-package-agreements
    }
    Refresh-Path
} else {
    Write-Host "  Node.js v$nodeVer 已安装，跳过"
}

Write-Host "  Node.js: $(node -v)"
Write-Host "  npm:     $(npm -v)"

Write-Host ""
Write-Host "  安装 OpenClaw..."
npm install -g openclaw@latest

Write-StepDone "Step 2 完成：Node.js $(node -v) + OpenClaw 已安装"

# ==========================================================
# Step 3: 配置 API Key 并启动 Gateway
# ==========================================================
Write-StepHeader "3/6" "配置 API Key 并启动 Gateway"

# 确保配置目录存在
$openclawHome = "$env:USERPROFILE\.openclaw"
if (-not (Test-Path $openclawHome)) {
    New-Item -ItemType Directory -Path $openclawHome -Force | Out-Null
}

# --- 配置 API Key ---
$envFile = "C:\openclaw\openclaw.env"
$envDir  = "C:\openclaw"
$needConfig = $true

if (-not (Test-Path $envDir)) {
    New-Item -ItemType Directory -Path $envDir -Force | Out-Null
}

if (Test-Path $envFile) {
    $envContent = Get-Content $envFile -Raw
    if ($envContent -match 'OPENAI_API_KEY=.+' -and $envContent -notmatch 'OPENAI_API_KEY=sk-xxx') {
        Write-Host "  API 配置已存在：" -ForegroundColor Yellow
        Write-Host "  ---"
        Get-Content $envFile | ForEach-Object { Write-Host "  $_" }
        Write-Host "  ---"
        $reconfig = Read-Host "  是否重新配置？[y/N]"
        if ($reconfig -notmatch '^[Yy]$') {
            $needConfig = $false
        }
    }
}

if ($needConfig) {
    Write-Host "  请选择你的大模型 API 提供商："
    Write-Host "    1) DeepSeek（推荐，国内性价比最高）"
    Write-Host "    2) 豆包（火山引擎）"
    Write-Host "    3) 通义千问（阿里云百炼）"
    Write-Host "    4) Kimi（Moonshot AI）"
    Write-Host "    5) OpenAI 官方"
    Write-Host "    6) 其他（手动输入 Base URL）"
    Write-Host ""
    $choice = Read-Host "  请输入编号 [1-6]（默认 1）"
    if ([string]::IsNullOrWhiteSpace($choice)) { $choice = "1" }

    switch ($choice) {
        "1" { $baseUrl = "https://api.deepseek.com/v1";                            $providerName = "DeepSeek" }
        "2" { $baseUrl = "https://ark.cn-beijing.volces.com/api/v3";               $providerName = "豆包" }
        "3" { $baseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1";      $providerName = "通义千问" }
        "4" { $baseUrl = "https://api.moonshot.cn/v1";                             $providerName = "Kimi" }
        "5" { $baseUrl = "https://api.openai.com/v1";                              $providerName = "OpenAI" }
        "6" { $baseUrl = Read-Host "  请输入 Base URL";                             $providerName = "自定义" }
        default { $baseUrl = "https://api.deepseek.com/v1";                        $providerName = "DeepSeek" }
    }

    Write-Host ""
    $apiKey = Read-Host "  请输入你的 ${providerName} API Key"
    if ([string]::IsNullOrWhiteSpace($apiKey)) {
        Write-Host "  API Key 不能为空，请重新运行脚本。" -ForegroundColor Red
        exit 1
    }

    @"
OPENAI_API_KEY=$apiKey
OPENAI_BASE_URL=$baseUrl
"@ | Out-File -FilePath $envFile -Encoding utf8 -Force

    Write-Host "  API 配置完成（${providerName}）" -ForegroundColor Green
}

# --- 配置 gateway 模式 ---
openclaw config set gateway.mode local 2>$null

# --- 注册为 Windows 服务（使用 NSSM） ---
Write-Host ""
Write-Host "  配置 Windows 服务..."

# 下载 NSSM（Non-Sucking Service Manager）
$nssmDir  = "C:\openclaw\nssm"
$nssmExe  = "$nssmDir\nssm.exe"

if (-not (Test-Path $nssmExe)) {
    Write-Host "  下载 NSSM（服务管理工具）..."
    $nssmZip = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $nssmZip -UseBasicParsing
    Expand-Archive -Path $nssmZip -DestinationPath "$env:TEMP\nssm-extract" -Force
    if (-not (Test-Path $nssmDir)) {
        New-Item -ItemType Directory -Path $nssmDir -Force | Out-Null
    }
    Copy-Item "$env:TEMP\nssm-extract\nssm-2.24\win64\nssm.exe" -Destination $nssmExe -Force
    Remove-Item $nssmZip, "$env:TEMP\nssm-extract" -Recurse -Force -ErrorAction SilentlyContinue
}

# 读取环境变量
$envVars = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        $envVars[$Matches[1]] = $Matches[2]
    }
}

# 获取 openclaw 实际路径
$openclawBin = (Get-Command openclaw -ErrorAction SilentlyContinue).Source
if (-not $openclawBin) {
    # npm 全局安装的 cmd 路径
    $openclawBin = Join-Path (npm prefix -g) "openclaw.cmd"
}

$serviceName = "OpenClawGateway"

# 如果服务已存在，先停止并删除
$existingService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "  检测到已有服务，正在更新..."
    & $nssmExe stop $serviceName 2>$null
    & $nssmExe remove $serviceName confirm 2>$null
    Start-Sleep -Seconds 2
}

# 检查端口占用
$portCheck = netstat -ano | Select-String ":18789\s+.*LISTENING"
if ($portCheck) {
    $portPid = ($portCheck -split '\s+')[-1]
    Write-Host "  端口 18789 被 PID $portPid 占用，正在清理..." -ForegroundColor Yellow
    Stop-Process -Id ([int]$portPid) -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# 创建一个启动脚本来加载环境变量后启动 openclaw
$launcherScript = "C:\openclaw\start-gateway.cmd"
@"
@echo off
set OPENAI_API_KEY=$($envVars['OPENAI_API_KEY'])
set OPENAI_BASE_URL=$($envVars['OPENAI_BASE_URL'])
set HOME=$env:USERPROFILE
"$openclawBin" gateway --port 18789
"@ | Out-File -FilePath $launcherScript -Encoding ascii -Force

# 用 NSSM 注册服务
& $nssmExe install $serviceName $launcherScript
& $nssmExe set $serviceName DisplayName "OpenClaw Gateway"
& $nssmExe set $serviceName Description "OpenClaw AI Gateway Service"
& $nssmExe set $serviceName AppStdout "C:\openclaw\logs\stdout.log"
& $nssmExe set $serviceName AppStderr "C:\openclaw\logs\stderr.log"
& $nssmExe set $serviceName AppRotateFiles 1
& $nssmExe set $serviceName AppRotateBytes 1048576
& $nssmExe set $serviceName Start SERVICE_AUTO_START

# 确保日志目录存在
if (-not (Test-Path "C:\openclaw\logs")) {
    New-Item -ItemType Directory -Path "C:\openclaw\logs" -Force | Out-Null
}

# 启动服务
& $nssmExe start $serviceName
Start-Sleep -Seconds 3

$svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-StepDone "Step 3 完成：Gateway 已启动并设为开机自启"
} else {
    Write-Host "  Gateway 启动可能需要几秒，请稍后运行：" -ForegroundColor Yellow
    Write-Host "    Get-Service $serviceName"
    Write-Host "    Get-Content C:\openclaw\logs\stderr.log -Tail 20"
    Write-Host ""
}

# ==========================================================
# Step 4: 安装 Tailscale
# ==========================================================
Write-StepHeader "4/6" "安装 Tailscale"

if (Get-Command tailscale -ErrorAction SilentlyContinue) {
    Write-Host "  Tailscale 已安装，跳过"
} else {
    Write-Host "  通过 winget 安装 Tailscale..."
    winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
    Refresh-Path
}

Write-StepDone "Step 4 完成：Tailscale 已安装"

# ==========================================================
# 自动化部分结束，输出后续手动操作指南
# ==========================================================
Write-Host ""
Write-Host ("=" * 50) -ForegroundColor Green
Write-Host "  $([char]0x2705) 自动化部署完成（Step 1-4）！" -ForegroundColor Green
Write-Host ("=" * 50) -ForegroundColor Green
Write-Host ""
Write-Host "  常用命令：" -ForegroundColor Yellow
Write-Host "    Get-Service OpenClawGateway                    # 查看 Gateway 状态"
Write-Host "    Get-Content C:\openclaw\logs\stderr.log -Tail 20  # 查看最近日志"
Write-Host "    Restart-Service OpenClawGateway                # 重启 Gateway"
Write-Host "    openclaw config get gateway                    # 查看 Gateway 配置"
Write-Host ""
Write-Host ""

Write-Host ("=" * 50) -ForegroundColor Yellow
Write-Host "  接下来请手动完成 Step 5（必须）和 Step 6（可选）" -ForegroundColor Yellow
Write-Host ("=" * 50) -ForegroundColor Yellow
Write-Host ""

Write-StepHeader "5/6" "配置 Tailscale Serve 与 Dashboard 访问"

Write-Host @"
  5.1  打开 Tailscale 客户端：
       在系统托盘（右下角）找到 Tailscale 图标，点击登录
       按提示在浏览器中完成认证

  5.2  开启 Tailscale Serve（以管理员 PowerShell 执行）：
       tailscale serve --bg 18789

  5.3  预获取 HTTPS 证书（重要！避免首次访问超时）：
       # 先获取你的 Tailscale 域名：
       tailscale status --self
       # 记录输出中的 DNS 名称，然后执行：
       tailscale cert <你的域名>

  5.4  配置 allowedOrigins（必须！否则浏览器访问会报 origin not allowed）：
       # 将 <你的域名> 替换为 5.3 中获取的 Tailscale 域名：
       openclaw config set gateway.controlUi.allowedOrigins '[\"http://localhost:18789\",\"http://127.0.0.1:18789\",\"https://<你的域名>\"]'
       Restart-Service OpenClawGateway

  5.5  获取 Dashboard 访问地址：
       openclaw dashboard --no-open
       # 将输出 URL 中的 127.0.0.1 替换为你的 Tailscale 域名

  5.6  首次浏览器访问需要设备配对：
       # 浏览器点 Connect 后如果提示 pairing required，执行：
       openclaw devices list
       openclaw devices approve <Request 列中的 ID>
       # 然后回浏览器重新点击 Connect
"@

Write-Host ""
Write-StepHeader "6/6" "配置防火墙（可选）"

Write-Host @"
  此步骤为安全加固，建议熟悉 Tailscale 后再操作。
  跳过不影响使用。

  6.1  确保 Tailscale 能正常连接后，可以阻止 18789 端口的公网访问：
       # 仅允许本机和 Tailscale 网段访问 Gateway
       New-NetFirewallRule -DisplayName "Block OpenClaw Public" ``
           -Direction Inbound -LocalPort 18789 -Protocol TCP ``
           -Action Block -Profile Public

  6.2  如果需要通过 Tailscale SSH 连接（Windows OpenSSH Server）：
       # 安装 OpenSSH Server（可选）
       Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
       Start-Service sshd
       Set-Service sshd -StartupType Automatic
"@

Write-Host ""
Write-Host @"

  注意事项：
  - 如果你使用了 Clash / V2Ray 等代理工具，
    需要配置 *.ts.net 和 100.0.0.0/8 走直连（DIRECT），
    否则浏览器可能无法通过 Tailscale 连接。
  - Windows Defender 防火墙可能弹出提示，请选择"允许访问"。
  - NSSM 服务日志位于 C:\openclaw\logs\ 目录。

"@ -ForegroundColor Yellow

Write-Host ("=" * 50) -ForegroundColor Cyan
