# scripts/start_all.ps1
# 职责：启动 Shelwell 全栈（首次部署 / 手动拉起用）
# 常驻运行由 Windows 服务托管：ShelwellOllama、ShelwellQQBot（NSSM）
# NapCat 容器由 Docker --restart=always 托管
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogFile = Join-Path $LogDir "launcher.log"

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

# ---------- 1. Docker Desktop ----------
$dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
if ($env:PATH -notlike "*$dockerBin*") { $env:PATH = "$dockerBin;$env:PATH" }

& docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Log "启动 Docker Desktop..."
    $dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dd) { Start-Process $dd }
    for ($i = 0; $i -lt 36; $i++) {
        Start-Sleep -Seconds 5
        & docker info 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
    }
}
& docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Log "ERROR: Docker 引擎未就绪"; exit 1 }
Log "Docker 引擎就绪"

# ---------- 2. Windows 服务 ----------
foreach ($svc in @("ShelwellOllama", "ShelwellQQBot")) {
    $state = (& sc.exe query $svc 2>&1 | Select-String "STATE").ToString()
    if ($state -match "RUNNING") { Log "$svc 已在运行" }
    else {
        Log "启动 $svc..."
        & sc.exe start $svc 2>&1 | Out-Null
        Start-Sleep -Seconds 3
        $state = (& sc.exe query $svc 2>&1 | Select-String "STATE").ToString()
        if ($state -match "RUNNING") { Log "$svc 已启动" } else { Log "WARN: $svc 启动异常" }
    }
}

# ---------- 3. NapCat 容器 ----------
$exists = (& docker inspect --format "{{.Name}}" napcat 2>&1) -match "napcat"
if (-not $exists) { Log "ERROR: napcat 容器不存在"; exit 1 }
$isRunning = (& docker inspect --format "{{.State.Running}}" napcat 2>&1).Trim() -eq "true"
if (-not $isRunning) {
    Log "启动 NapCat 容器..."
    & docker start napcat 2>&1 | Out-Null
    Start-Sleep -Seconds 5
}
$isRunning = (& docker inspect --format "{{.State.Running}}" napcat 2>&1).Trim() -eq "true"
if ($isRunning) { Log "NapCat 容器运行中" } else { Log "ERROR: NapCat 容器启动失败" }

# ---------- 4. 状态汇总 ----------
Log "----- 状态汇总 -----"
Log ("Ollama 11434: " + (Test-NetConnection -ComputerName 127.0.0.1 -Port 11434 -WarningAction SilentlyContinue -InformationLevel Quiet))
Log ("QQBot 8765:  " + (Test-NetConnection -ComputerName 127.0.0.1 -Port 8765 -WarningAction SilentlyContinue -InformationLevel Quiet))
Log "注意：QQ 被风控踢下线时需在 NapCat WebUI 重新扫码：http://127.0.0.1:6099/webui"