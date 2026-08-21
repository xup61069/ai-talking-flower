# AEC DLL 交付管線：下載 webrtc-apm.dll 並以 SHA256 校驗。
# 用法：./tools/fetch_native.ps1 [-Source <url-or-path>]
# 若 -Source 省略，依序嘗試：
#   1. 官方 release URL（$ReleaseUrl，尚未發布時可留空）
#   2. 本機備份路徑 $LocalFallback
param(
    [string]$Source = ""
)

$ErrorActionPreference = "Stop"

$DestDir = Join-Path $PSScriptRoot "..\native"
$Dest = Join-Path $DestDir "webrtc-apm.dll"
$ExpectedSha256 = "4631e83f19d383ff22507728db7a1f8efb7fe726d8d8176730b76b79ef5db551"

# 已校驗通過的本地副本候選（換機器時把 DLL 放在這裡其中一處即可）
$LocalFallback = @(
    (Join-Path $PSScriptRoot "native-backup\webrtc-apm.dll"),
    (Join-Path ([Environment]::GetFolderPath("UserProfile")) "Downloads\webrtc-apm.dll")
)

# 官方 release URL：發布 v0.1.0 後填入 GitHub Release 資產連結
$ReleaseUrl = ""

function Test-Sha256([string]$Path, [string]$Expected) {
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
    return $actual -eq $Expected.ToLower()
}

if (Test-Path -LiteralPath $Dest) {
    if (Test-Sha256 $Dest $ExpectedSha256) {
        Write-Host "[OK] native/webrtc-apm.dll 已存在且校驗通過" -ForegroundColor Green
        exit 0
    } else {
        Write-Warning "現有 DLL 校驗不符，將重新取得"
        Remove-Item -LiteralPath $Dest -Force
    }
}

New-Item -ItemType Directory -Force -Path $DestDir | Out-Null

$candidates = @()
if ($Source) { $candidates += @{ Type = "Any"; Value = $Source } }
if ($ReleaseUrl) { $candidates += @{ Type = "Url"; Value = $ReleaseUrl } }
foreach ($local in $LocalFallback) {
    if ($local) { $candidates += @{ Type = "Path"; Value = $local } }
}

foreach ($candidate in $candidates) {
    try {
        switch ($candidate.Type) {
            "Url" {
                Write-Host "下載中：$($candidate.Value)"
                Invoke-WebRequest -Uri $candidate.Value -OutFile $Dest -UseBasicParsing
            }
            "Path" {
                if (-not (Test-Path -LiteralPath $candidate.Value)) { continue }
                Write-Host "複製自本機備份：$($candidate.Value)"
                Copy-Item -LiteralPath $candidate.Value -Destination $Dest -Force
            }
            "Any" {
                if ($candidate.Value -match "^https?://") {
                    Write-Host "下載中：$($candidate.Value)"
                    Invoke-WebRequest -Uri $candidate.Value -OutFile $Dest -UseBasicParsing
                } else {
                    if (-not (Test-Path -LiteralPath $candidate.Value)) {
                        throw "找不到來源檔：$($candidate.Value)"
                    }
                    Write-Host "複製自指定路徑：$($candidate.Value)"
                    Copy-Item -LiteralPath $candidate.Value -Destination $Dest -Force
                }
            }
        }
        if (Test-Sha256 $Dest $ExpectedSha256) {
            Write-Host "[OK] webrtc-apm.dll 已就位，SHA256 校驗通過" -ForegroundColor Green
            exit 0
        }
        Write-Warning "SHA256 不符，捨棄此次取得的檔案"
        Remove-Item -LiteralPath $Dest -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "此來源失敗：$_"
    }
}

Write-Host ""
Write-Host "無法自動取得 webrtc-apm.dll。請手動處理：" -ForegroundColor Yellow
Write-Host "  1. 到專案的 GitHub Releases 下載 webrtc-apm.dll（若已發布）"
Write-Host "  2. 或從其他可用機器複製，放到 tools\native-backup\webrtc-apm.dll"
Write-Host "  3. 再執行一次 ./tools/fetch_native.ps1"
Write-Host ""
Write-Host "沒有此 DLL 時 AEC 將無法啟用（程式仍可執行，但回音消除關閉）。" -ForegroundColor Yellow
exit 1
