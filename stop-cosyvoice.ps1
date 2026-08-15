$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $projectRoot 'data\cosyvoice.pid'

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host 'CosyVoice3: no background service record'
    return
}

$servicePid = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $servicePid" -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host 'CosyVoice3: stopped'
    return
}
if ($process.CommandLine -notlike '*cosyvoice_server.py*') {
    throw "PID $servicePid 並非花花的 CosyVoice 服務，已停止操作"
}

Stop-Process -Id $servicePid
Remove-Item -LiteralPath $pidFile -Force
Write-Host 'CosyVoice3: stopped'
