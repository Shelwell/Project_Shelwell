# scripts/set_ollama_env.ps1
# Purpose: enable KV cache quantization + FlashAttention for the ShelwellOllama service.
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File E:\Shelwell\scripts\set_ollama_env.ps1
# Dry run (no changes, safe for everyone):
#   powershell -ExecutionPolicy Bypass -File E:\Shelwell\scripts\set_ollama_env.ps1 -DryRun
#
# Why:
#   OLLAMA_KV_CACHE_TYPE=q8_0     KV cache 8-bit quantization, about half the VRAM
#   OLLAMA_FLASH_ATTENTION=1      prerequisite for quantized KV, also cheaper long context

param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$service = "ShelwellOllama"

$nssm = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\nssm.exe"
if (-not (Test-Path $nssm)) {
    $found = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if ($found) { $nssm = $found.Source }
}
if (-not (Test-Path $nssm)) {
    throw "nssm.exe not found. Install NSSM or edit this script."
}

$envList = @(
    "OLLAMA_MODELS=E:\Shelwell\Ollama_models",
    "OLLAMA_MAX_LOADED_MODELS=2",
    "OLLAMA_KV_CACHE_TYPE=q8_0",
    "OLLAMA_FLASH_ATTENTION=1"
)

Write-Host "Service : $service"
Write-Host "NSSM    : $nssm"
Write-Host "Env vars to apply:"
foreach ($line in $envList) { Write-Host "  $line" }

if ($DryRun) {
    Write-Host ""
    Write-Host "DryRun: nothing changed." -ForegroundColor Yellow
    exit 0
}

& $nssm set $service AppEnvironmentExtra $envList
if ($LASTEXITCODE -ne 0) { throw "nssm set failed with exit code $LASTEXITCODE" }

Write-Host ""
Write-Host "Restarting service ..." -ForegroundColor Yellow
Restart-Service $service -Force
Start-Sleep -Seconds 4

$status = (Get-Service $service).Status
Write-Host "Service status: $status" -ForegroundColor Green

try {
    $ver = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 5
    Write-Host "Ollama version: $($ver.version)"
} catch {
    Write-Host "Ollama not responding yet, wait a few seconds." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Verify: send one message, then check 'KV buffer size' in Ollama log"
Write-Host "(should be about half of before), or: curl http://127.0.0.1:11434/api/ps"
