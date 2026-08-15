$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $projectRoot 'data\kokoro.pid'

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host 'Kokoro TTS: not running'
    return
}

$processId = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
if ($null -ne $process -and $process.CommandLine -like '*kokoro_server.py*') {
    Stop-Process -Id $processId -Force
    Write-Host 'Kokoro TTS: stopped'
}
else {
    Write-Host 'Kokoro TTS: stale PID file removed'
}
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
