# 启动后端（默认 mock 模式，无需 LLM Key）
# 用法：在项目根目录 PowerShell 里执行  .\scripts\start-backend.ps1
# 或：  .\scripts\start-backend.ps1 -NoMock   （关闭 mock，专测 502）

param(
    [switch]$NoMock,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# 本机唯一装了项目依赖的解释器
$Python = "D:\Python314\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "[错误] 找不到 $Python" -ForegroundColor Red
    Write-Host "       项目依赖装在这个 Python 3.14 里，请确认路径。" -ForegroundColor Red
    exit 1
}

if (-not $NoMock) {
    $env:LLM_MOCK = "true"
    Write-Host "[提示] 已启用 LLM_MOCK=true（回显模式，无需 API Key）" -ForegroundColor Yellow
} else {
    Remove-Item Env:\LLM_MOCK -ErrorAction SilentlyContinue
    Write-Host "[提示] 已关闭 LLM_MOCK（无 Key 时发消息会 502，用于验收错误分支）" -ForegroundColor Yellow
}

Write-Host "[启动] $Python -m uvicorn app.main:app --port $Port" -ForegroundColor Cyan
& $Python -m uvicorn app.main:app --port $Port --log-level info
