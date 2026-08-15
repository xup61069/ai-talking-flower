$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $projectRoot

$usesCosyVoice = Select-String -LiteralPath (Join-Path $projectRoot 'config.toml') -Pattern '^backend\s*=\s*"cosyvoice"' -Quiet
$usesKokoro = Select-String -LiteralPath (Join-Path $projectRoot 'config.toml') -Pattern '^backend\s*=\s*"kokoro"' -Quiet
if ($usesKokoro) {
    & (Join-Path $projectRoot 'start-kokoro.ps1')
}
elseif ($usesCosyVoice) {
    & (Join-Path $projectRoot 'start-cosyvoice.ps1')
}

python -m talking_flower @args
