$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$cosyRoot = Join-Path $projectRoot 'third_party\CosyVoice'
$serverScript = Join-Path $projectRoot 'tools\cosyvoice_server.py'
if ($env:FLOWER_TTS_PYTHON) {
    $python = $env:FLOWER_TTS_PYTHON
}
elseif (Test-Path 'C:\Users\Administrator\miniconda3\envs\flower-tts\python.exe') {
    $python = 'C:\Users\Administrator\miniconda3\envs\flower-tts\python.exe'
}
else {
    $python = 'python'
}
$modelDir = Join-Path $cosyRoot 'pretrained_models\Fun-CosyVoice3-0.5B'
$promptWav = Join-Path $cosyRoot 'asset\zero_shot_prompt.wav'
$promptText = 'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。'
$styleFile = Join-Path $projectRoot 'voice\style.txt'
$voiceSelection = Join-Path $projectRoot 'voice\active.json'
$dataDir = Join-Path $projectRoot 'data'
$stdoutLog = Join-Path $dataDir 'cosyvoice.stdout.log'
$stderrLog = Join-Path $dataDir 'cosyvoice.stderr.log'
$pidFile = Join-Path $dataDir 'cosyvoice.pid'
$healthUrl = 'http://127.0.0.1:50000/health'

if (Test-Path -LiteralPath $voiceSelection) {
    $selection = Get-Content -LiteralPath $voiceSelection -Raw -Encoding utf8 | ConvertFrom-Json
    if ($selection.prompt_file) {
        $candidate = Join-Path $projectRoot $selection.prompt_file
        if (Test-Path -LiteralPath $candidate) {
            $promptWav = (Resolve-Path -LiteralPath $candidate).Path
            Write-Host "CosyVoice3 voice: $($selection.name)"
        }
    }
    if ($selection.prompt_text) {
        $promptText = [string]$selection.prompt_text
    }
}

function Test-CosyVoice {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $health.status -eq 'ok'
    }
    catch {
        return $false
    }
}

if (Test-CosyVoice) {
    Write-Host 'CosyVoice3: ready'
    return
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "找不到 CosyVoice Python 環境：$python"
}
if (-not (Test-Path -LiteralPath $modelDir)) {
    throw "找不到 CosyVoice3 模型：$modelDir"
}

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$argList = "-X utf8 `"$serverScript`" --cosyvoice-root `"$cosyRoot`" --model-dir `"$modelDir`" --prompt-wav `"$promptWav`" --prompt-text `"$promptText`" --style-file `"$styleFile`" --flow-steps 6 --host 127.0.0.1 --port 50000"

Write-Host 'CosyVoice3: loading (about 30 seconds on first start)...'
$process = Start-Process -FilePath $python -ArgumentList $argList -WorkingDirectory $cosyRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii

$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
    if (Test-CosyVoice) {
        Write-Host 'CosyVoice3: ready'
        return
    }
    if ($process.HasExited) {
        $details = if (Test-Path -LiteralPath $stderrLog) { (Get-Content -LiteralPath $stderrLog -Tail 25) -join "`n" } else { '' }
        throw "CosyVoice3 啟動失敗。`n$details"
    }
    Start-Sleep -Milliseconds 750
}

throw "CosyVoice3 在 120 秒內沒有完成載入；請查看 $stderrLog"
