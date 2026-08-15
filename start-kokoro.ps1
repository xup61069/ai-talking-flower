$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverScript = Join-Path $projectRoot 'tools\kokoro_server.py'
if ($env:FLOWER_KOKORO_PYTHON) {
    $python = $env:FLOWER_KOKORO_PYTHON
}
elseif (Test-Path 'C:\Users\Administrator\miniconda3\envs\flower-kokoro\python.exe') {
    $python = 'C:\Users\Administrator\miniconda3\envs\flower-kokoro\python.exe'
}
else {
    $python = 'python'
}
$dataDir = Join-Path $projectRoot 'data'
$stdoutLog = Join-Path $dataDir 'kokoro.stdout.log'
$stderrLog = Join-Path $dataDir 'kokoro.stderr.log'
$pidFile = Join-Path $dataDir 'kokoro.pid'
$healthUrl = 'http://127.0.0.1:50001/health'

function Test-Kokoro {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $health.status -eq 'ok'
    }
    catch {
        return $false
    }
}

if (Test-Kokoro) {
    Write-Host 'Kokoro TTS: ready'
    return
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "找不到 Kokoro Python 環境：$python"
}

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$arguments = @(
    '-X', 'utf8',
    $serverScript,
    '--device', 'cuda',
    '--host', '127.0.0.1',
    '--port', '50001'
)

Write-Host 'Kokoro TTS: loading...'
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii

$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    if (Test-Kokoro) {
        Write-Host 'Kokoro TTS: ready'
        return
    }
    if ($process.HasExited) {
        $details = if (Test-Path -LiteralPath $stderrLog) { (Get-Content -LiteralPath $stderrLog -Tail 25) -join "`n" } else { '' }
        throw "Kokoro TTS 啟動失敗。`n$details"
    }
    Start-Sleep -Milliseconds 500
}

throw "Kokoro TTS 在 90 秒內沒有完成載入；請查看 $stderrLog"
