$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $projectRoot

$backend = ''
$settingsJson = Join-Path $projectRoot 'data\settings.json'
if (Test-Path -LiteralPath $settingsJson) {
    try {
        $settings = Get-Content -LiteralPath $settingsJson -Raw -Encoding utf8 | ConvertFrom-Json
        $override = [string]$settings.'tts.backend'
        if ($override -in @('kokoro', 'cosyvoice')) { $backend = $override }
    }
    catch { }
}
if (-not $backend) {
    if (Select-String -LiteralPath (Join-Path $projectRoot 'config.toml') -Pattern '^backend\s*=\s*"kokoro"' -Quiet) {
        $backend = 'kokoro'
    }
    elseif (Select-String -LiteralPath (Join-Path $projectRoot 'config.toml') -Pattern '^backend\s*=\s*"cosyvoice"' -Quiet) {
        $backend = 'cosyvoice'
    }
}
if ($backend -eq 'kokoro') {
    & (Join-Path $projectRoot 'start-kokoro.ps1')
}
elseif ($backend -eq 'cosyvoice') {
    & (Join-Path $projectRoot 'start-cosyvoice.ps1')
}

python -m talking_flower @args
