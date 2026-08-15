$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$projectRoot = $env:FLOWER_PROJECT_ROOT
if (-not $projectRoot) {
    $projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$projectRoot = $projectRoot.TrimEnd('\')
$candidateRoot = Join-Path $projectRoot 'voice\candidates\TTS-SCDuFSC'
$manifestPath = Join-Path $candidateRoot 'voices.json'
$activePath = Join-Path $projectRoot 'voice\active.json'
$configPath = Join-Path $projectRoot 'config.toml'
$usesKokoro = Select-String -LiteralPath $configPath -Pattern '^backend\s*=\s*"kokoro"' -Quiet
if ($usesKokoro) {
    Write-Host ''
    Write-Host '目前是 Kokoro 快速模式，音色為 zf_001。'
    Write-Host '這個六音色選單只會更換 CosyVoice 高品質模式的參考聲音，因此沒有變更設定。'
    return
}
$parsedVoices = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
$voices = New-Object 'System.Collections.Generic.List[object]'
foreach ($item in $parsedVoices) {
    $voices.Add($item)
}

Write-Host ''
Write-Host '會依序播放六個女聲樣本。每段前會顯示編號，聽完再選。'
Write-Host ''
for ($index = 0; $index -lt $voices.Count; $index++) {
    $voice = $voices[$index]
    Write-Host "[$($index + 1)] $($voice.voice)  $($voice.transcript)"
    $samplePath = Join-Path -Path $candidateRoot -ChildPath ([string]$voice.file)
    $player = New-Object System.Media.SoundPlayer -ArgumentList $samplePath
    $player.PlaySync()
    Start-Sleep -Milliseconds 250
}

Write-Host '[0] 保留／恢復官方示範音色'
$choice = Read-Host '請輸入 0 到 6'
if ($choice -eq '0') {
    if (Test-Path -LiteralPath $activePath) {
        Remove-Item -LiteralPath $activePath
    }
    Write-Host '已選擇官方示範音色。'
}
elseif ($choice -match '^[1-6]$') {
    $voice = $voices[[int]$choice - 1]
    [pscustomobject]@{
        name = $voice.voice
        prompt_file = "voice/candidates/TTS-SCDuFSC/$($voice.file)"
        transcript = $voice.transcript
    } | ConvertTo-Json | Set-Content -LiteralPath $activePath -Encoding utf8
    Write-Host "已選擇 $($voice.voice)。"
}
else {
    throw '選項無效，沒有變更音色。'
}

& (Join-Path $projectRoot 'stop-cosyvoice.ps1')
& (Join-Path $projectRoot 'start-cosyvoice.ps1')
