# Restore desktop shortcuts from Start Menu and common apps.
$ErrorActionPreference = "Continue"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ProjectRoot = Split-Path $PSScriptRoot -Parent

function New-DesktopShortcut([string]$Name, [string]$Target, [string]$WorkDir = "") {
    $base = [System.IO.Path]::GetFileNameWithoutExtension($Name)
    $path = Join-Path $Desktop "$base.lnk"
    $n = 1
    while (Test-Path $path) {
        $path = Join-Path $Desktop "$base ($n).lnk"
        $n++
    }
    $Wsh = New-Object -ComObject WScript.Shell
    $lnk = $Wsh.CreateShortcut($path)
    $lnk.TargetPath = $Target
    if ($WorkDir) { $lnk.WorkingDirectory = $WorkDir }
    $lnk.Save()
    Write-Host "  + $(Split-Path $path -Leaf) -> $Target"
}

Write-Host "=== Restore Desktop Shortcuts ===" -ForegroundColor Cyan
Write-Host "Desktop: $Desktop"

Write-Host ""
Write-Host "[1] Copy from Start Menu..." -ForegroundColor Yellow
$startPaths = @(
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
    "$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
    "C:\Users\Public\Desktop"
)
$seen = @{}
$copied = 0
foreach ($root in $startPaths) {
    if (-not (Test-Path $root)) { continue }
    Get-ChildItem $root -Recurse -Filter "*.lnk" -ErrorAction SilentlyContinue | ForEach-Object {
        $key = $_.Name.ToLower()
        if ($seen.ContainsKey($key)) { return }
        $seen[$key] = $true
        $dest = Join-Path $Desktop $_.Name
        $n = 1
        while (Test-Path $dest) {
            $base = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
            $ext = [System.IO.Path]::GetExtension($_.Name)
            $dest = Join-Path $Desktop ($base + " (" + $n + ")" + $ext)
            $n++
        }
        try {
            Copy-Item $_.FullName $dest -Force
            Write-Host "  + $(Split-Path $dest -Leaf)"
            $script:copied++
        } catch {
            Write-Host "  ! skip $($_.Name)"
        }
    }
}
Write-Host "Copied $copied shortcuts"

Write-Host ""
Write-Host "[2] Common apps..." -ForegroundColor Yellow
$apps = @(
    @("Google Chrome", "C:\Program Files\Google\Chrome\Application\chrome.exe"),
    @("Google Chrome x86", "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    @("Microsoft Edge", "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    @("WeChat", "C:\Program Files (x86)\Tencent\WeChat\WeChat.exe"),
    @("WXWork", "C:\Program Files (x86)\WXWork\WXWork.exe"),
    @("Cursor", "$env:LOCALAPPDATA\Programs\cursor\Cursor.exe"),
    @("VS Code", "$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe"),
    @("Notepad", "C:\Windows\System32\notepad.exe"),
    @("PowerShell", "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
)
foreach ($app in $apps) {
    $name = $app[0]
    $path = $app[1]
    if (Test-Path $path) {
        New-DesktopShortcut $name $path
    }
}

Write-Host ""
Write-Host "[3] Project shortcuts..." -ForegroundColor Yellow
$startBat = Join-Path $ProjectRoot "start.bat"
$pullBat = Join-Path $ProjectRoot "pull_remote.bat"
if (Test-Path $startBat) { New-DesktopShortcut "WeCom Chat Parser" $startBat $ProjectRoot }
if (Test-Path $pullBat) { New-DesktopShortcut "Pull Remote WeCom" $pullBat $ProjectRoot }

Write-Host ""
Write-Host "[4] Installed programs..." -ForegroundColor Yellow
$regPaths = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
)
$progCount = 0
foreach ($item in (Get-ItemProperty $regPaths -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName })) {
    $icon = ($item.DisplayIcon -split ",")[0].Trim('"')
    if (-not $icon -or -not (Test-Path $icon)) { continue }
    $safe = ($item.DisplayName -replace '[\\/:*?"<>|]', '_')
    if ($safe.Length -gt 50) { $safe = $safe.Substring(0, 50) }
    $dest = Join-Path $Desktop ($safe + ".lnk")
    if (Test-Path $dest) { continue }
    if ($progCount -ge 30) { break }
    try {
        New-DesktopShortcut $safe $icon
        $progCount++
    } catch {}
}

$count = (Get-ChildItem $Desktop -Force).Count
Write-Host ""
Write-Host "Done. Desktop now has $count items." -ForegroundColor Green
Write-Host "For deleted personal files, run Recuva as Administrator:" -ForegroundColor Yellow
Write-Host ("  " + $env:TEMP + "\recuva_setup.exe") -ForegroundColor Yellow
