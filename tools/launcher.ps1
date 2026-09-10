# ============================================================================
#  智工云枢（ICOPS）MVP · Windows 一键启动器（本地 Harness）
#  双击本脚本（或 powershell -ExecutionPolicy Bypass -File tools\launcher.ps1）
#  功能：检查环境 →（可选）一键初始化演示数据 → 拉起后端并自动打开浏览器
#         http://127.0.0.1:<PORT>/ （前端 dist 由后端统一托管）
# ============================================================================
param([int]$Port = 8000)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
$pidFile = Join-Path $root 'data\run\backend.pid'
New-Item -ItemType Directory -Force -Path (Join-Path $root 'data\run') | Out-Null
$logFile = Join-Path $root 'data\run\backend.log'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Ask($msg) {
  Write-Host ''
  Write-Host $msg
  Write-Host '  1) 启动并打开浏览器（推荐，首次会自动初始化数据）'
  Write-Host '  2) 一键初始化演示数据（知识库+模拟数据+模型，可重复执行）'
  Write-Host '  3) 重新构建前端（frontend -> dist）'
  Write-Host '  4) 停止后端'
  Write-Host '  0) 退出'
  Write-Host ''
  return Read-Host '请输入序号'
}

function BackendRunning {
  try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 3
    return ($h.app -eq 'icops-backend')
  } catch { return $false }
}

function InitData {
  Write-Step '一键初始化演示数据（build_kb → 模拟数据 → 装载 → 质检 → 训练模型）'
  & $py -m scripts.build_kb;       if ($LASTEXITCODE -ne 0) { throw 'build_kb 失败' }
  & $py -m data.simulator.gen --days 30; if ($LASTEXITCODE -ne 0) { throw '数据生成失败' }
  & $py -m scripts.load_demo;      if ($LASTEXITCODE -ne 0) { throw 'load_demo 失败' }
  & $py -m scripts.quality_check --strict; if ($LASTEXITCODE -ne 0) { Write-Host '质检未达标（警告）' -ForegroundColor Yellow }
  & $py -m scripts.train_models;   if ($LASTEXITCODE -ne 0) { Write-Host '模型训练未达标（警告）' -ForegroundColor Yellow }
  Write-Step '演示数据初始化完成 ✓'
}

function BuildFrontend {
  Write-Step '构建前端（npm install + npm run build）'
  Push-Location (Join-Path $root 'frontend')
  try {
    if (-not (Test-Path 'node_modules')) { npm install --no-audit --no-fund | Out-Null }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
  } finally { Pop-Location }
  Write-Step '前端构建完成 ✓（dist 由后端托管）'
}

function StartBackend {
  if (BackendRunning) {
    Write-Step "后端已在 $Port 端口运行，无需重复启动"
  } else {
    $args = @('-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', "$Port")
    $proc = Start-Process -FilePath $py -ArgumentList $args -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError ($logFile + '.err') -PassThru
    Set-Content -Path $pidFile -Value $proc.Id
    Write-Step "后端启动中 PID=$($proc.Id)，日志：$logFile"
    for ($i = 0; $i -lt 30; $i++) {
      Start-Sleep -Milliseconds 700
      if (BackendRunning) { break }
    }
    if (-not (BackendRunning)) {
      Write-Host '后端启动超时，请查看日志：' -ForegroundColor Yellow
      if (Test-Path ($logFile + '.err')) { Get-Content ($logFile + '.err') -Tail 20 }
      throw '后端启动失败'
    }
  }
  # 演示库就绪性兜底：业务表为空则提示初始化
  $ready = & $py -c "from backend.app.core.db import SessionLocal; from backend.app.models import Device, EquipmentModel; d=SessionLocal(); c=(d.query(Device).count(), d.query(EquipmentModel).count()); d.close(); print(1 if c[0]>0 and c[1]>0 else 0)"
  if ($ready.Trim() -ne '1') {
    Write-Host '检测到业务演示数据为空，建议先执行「一键初始化演示数据」。' -ForegroundColor Yellow
  }
  Write-Step "浏览器打开：http://127.0.0.1:$Port/ （账号 admin / icops2026）"
  Start-Process "http://127.0.0.1:$Port/"
}

function StopBackend {
  if (Test-Path $pidFile) {
    $pidOld = Get-Content $pidFile
    Stop-Process -Id $pidOld -Force -ErrorAction SilentlyContinue
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    Write-Step '后端已停止'
  } else { Write-Host '未发现运行中的后端 PID 文件' -ForegroundColor Yellow }
}

Write-Host ''
Write-Host '======================================================'
Write-Host '  智工云枢 ICOPS · AI 原生工程机械智能运营平台'
Write-Host '  本地一键启动（Harness）  '
Write-Host "  端口：$Port    目录：$root"
Write-Host '======================================================'
Write-Host ''

if (-not (Test-Path $py)) {
  Write-Host '未找到 .venv，请先执行：uv sync' -ForegroundColor Red
  Read-Host '按回车退出'
  exit 1
}

while ($true) {
  $cmd = Ask '请选择操作：'
  try {
    switch ($cmd) {
      '1' {
        if (-not (Test-Path (Join-Path $root 'frontend\dist\index.html'))) { BuildFrontend }
        # 首次：若业务库为空则自动初始化（保证开箱即演示）
        if (-not (Test-Path (Join-Path $root 'data\icops.db'))) {
          Write-Step '首次运行：自动初始化演示数据'
          InitData
        }
        StartBackend
        if ((Read-Host '按回车返回菜单') -eq $null) { }
      }
      '2' { InitData }
      '3' { BuildFrontend }
      '4' { StopBackend }
      '0' { StopBackend; Write-Host '再见！'; break }
      default { Write-Host '无效输入' -ForegroundColor Yellow }
    }
  } catch {
    Write-Host "操作失败：$_" -ForegroundColor Red
  }
}
