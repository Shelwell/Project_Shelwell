# scripts/set_ollama_env.ps1
# 职责：给 ShelwellOllama 服务开启 KV 缓存量化与 FlashAttention，然后重启服务
# 用法：右键“以管理员身份运行 PowerShell”，执行：
#       powershell -ExecutionPolicy Bypass -File E:\Shelwell\scripts\set_ollama_env.ps1
# 说明：
#   OLLAMA_KV_CACHE_TYPE=q8_0      KV 缓存 8bit 量化，显存占用减半（质量几乎无损）
#   OLLAMA_FLASH_ATTENTION=1       量化 KV 的前提，同时降低长上下文显存
#   已有环境变量会保留，只覆盖上面两个键

$ErrorActionPreference = "Stop"
$svc = "ShelwellOllama"

# 定位 nssm（WinGet Links 或 PATH）
$nssm = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\nssm.exe"
if (-not (Test-Path $nssm)) {
    $cmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if ($cmd) { $nssm = $cmd.Source }
}
if (-not $nssm -or -not (Test-Path $nssm)) {
    throw "找不到 nssm.exe，请先确认服务是用 NSSM 安装的"
}

$key = "HKLM:\SYSTEM\CurrentControlSet\Services\$svc\Parameters"
$cur = (Get-ItemProperty -Path $key -Name AppEnvironmentExtra -ErrorAction SilentlyContinue).AppEnvironmentExtra

$want = @{
    "OLLAMA_KV_CACHE_TYPE"   = "q8_0"
    "OLLAMA_FLASH_ATTENTION" = "1"
}

$lines = @()
if ($cur) {
    $lines = @($cur | Where-Object { $_ -and $_.Trim().Length -gt 0 })
}
$kept = @()
foreach ($line in $lines) {
    $k = ($line -split "=", 2)[0].Trim()
    if (-not $want.ContainsKey($k)) { $kept += $line.Trim() }
}
foreach ($k in $want.Keys) { $kept += "$k=$($want[$k])" }

Write-Host "写入 AppEnvironmentExtra:" -ForegroundColor Green
$kept | ForEach-Object { Write-Host "  $_" }

& $nssm set $svc AppEnvironmentExtra @kept | Out-Null
if ($LASTEXITCODE -ne 0) { throw "nssm set 失败（退出码 $LASTEXITCODE）" }

Write-Host "重启服务 $svc ..." -ForegroundColor Yellow
Restart-Service $svc -Force
Start-Sleep -Seconds 4

$st = (Get-Service $svc).Status
Write-Host "服务状态: $st" -ForegroundColor Green
try {
    $v = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 5
    Write-Host "Ollama 版本: $($v.version)"
} catch {
    Write-Host "Ollama 尚未响应，稍等几秒再试" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "验证 KV 量化是否生效：" -ForegroundColor Cyan
Write-Host "  1) 发一条消息触发加载"
Write-Host "  2) 查看日志中的 'KV buffer size'（应约为原来的一半）"
Write-Host "  3) curl http://127.0.0.1:11434/api/ps 看 size_vram"
