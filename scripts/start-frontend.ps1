# 启动前端 dev server（验收台）
# 用法：在项目根目录 PowerShell 里执行  .\scripts\start-frontend.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WebDir = Join-Path $ProjectRoot "web"
Set-Location $WebDir

# 本机 npm 在 WorkBuddy 内置 node 里，系统 PATH 里没有，这里用全路径
$Npm = "C:\Users\xike\.workbuddy\binaries\node\versions\22.22.2-6\npm.cmd"
if (-not (Test-Path $Npm)) {
    Write-Host "[错误] 找不到 $Npm" -ForegroundColor Red
    Write-Host "       npm 随 WorkBuddy 内置 node 安装，请确认该路径。" -ForegroundColor Red
    exit 1
}

Write-Host "[启动] $Npm run dev" -ForegroundColor Cyan
Write-Host "[提示] 浏览器打开 http://localhost:5173" -ForegroundColor Yellow
& $Npm run dev
