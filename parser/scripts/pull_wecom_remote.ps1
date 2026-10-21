# Pull WeCom chat data from a remote Windows PC, decrypt there, sync to local export/.
param(
    [string]$RemoteHost = "192.168.2.14",
    [string]$RemoteUser = "mache",
    [string]$RemotePassword = "machefu666",
    [switch]$IncludeCache,
    [int]$SshPort = 22
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$ToolsDir = Join-Path $ProjectRoot "tools\wechat-decrypt"
$LocalDecrypted = Join-Path $ProjectRoot "export\wxwork_decrypted"
$LocalRemoteRoot = Join-Path $ProjectRoot "export\wxwork_remote"
$RemoteWorkRoot = "C:\wxwork_pull"
$RemoteToolsDir = "$RemoteWorkRoot\tools"
$RemoteOutputDir = "$RemoteWorkRoot\decrypted"

function Ensure-PoshSSH {
    if (Get-Module -ListAvailable -Name Posh-SSH) {
        Import-Module Posh-SSH -ErrorAction Stop
        return
    }
    Write-Host "[*] Installing Posh-SSH module..." -ForegroundColor Yellow
    if (-not (Get-PackageProvider -Name NuGet -ErrorAction SilentlyContinue)) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force | Out-Null
    }
    Install-Module Posh-SSH -Scope CurrentUser -Force -AllowClobber
    Import-Module Posh-SSH -ErrorAction Stop
}

function New-RemoteCredential([string]$User, [string]$Password) {
    return New-Object System.Management.Automation.PSCredential(
        $User,
        (ConvertTo-SecureString $Password -AsPlainText -Force)
    )
}

function Invoke-RemoteCommand([object]$Session, [string]$Command) {
    $result = Invoke-SSHCommand -SessionId $Session.SessionId -Command $Command -TimeOut 600
    if ($result.ExitStatus -ne 0) {
        $detail = ($result.Output + $result.Error) -join "`n"
        throw "Remote command failed ($($result.ExitStatus)): $detail"
    }
    return $result.Output
}

function Upload-ToolsPackage([object]$Session, [string]$LocalToolsDir, [string]$RemoteToolsDir) {
    $zipPath = Join-Path $env:TEMP ("wxwork_tools_{0}.zip" -f ([guid]::NewGuid().ToString("N")))
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

    $staging = Join-Path $env:TEMP ("wxwork_tools_stage_{0}" -f ([guid]::NewGuid().ToString("N")))
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    Copy-Item -Path (Join-Path $LocalToolsDir "*") -Destination $staging -Recurse -Force
    Get-ChildItem -Path $staging -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zipPath -Force
    Remove-Item $staging -Recurse -Force

    Invoke-RemoteCommand $Session "powershell -NoProfile -Command `"New-Item -ItemType Directory -Force -Path '$RemoteToolsDir' | Out-Null`""

    Set-SCPItem -SessionId $Session.SessionId -Path $zipPath -Destination "$RemoteWorkRoot\tools.zip" -AcceptKey
    Invoke-RemoteCommand $Session @"
powershell -NoProfile -Command "Expand-Archive -Path '$RemoteWorkRoot\tools.zip' -DestinationPath '$RemoteToolsDir' -Force"
"@

    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
}

function Download-RemoteFolder([object]$Session, [string]$RemotePath, [string]$LocalPath) {
    New-Item -ItemType Directory -Force -Path $LocalPath | Out-Null
    Get-SCPItem -SessionId $Session.SessionId -Path $RemotePath -Destination $LocalPath -Recurse -AcceptKey
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Pull WeCom data from $RemoteUser@$RemoteHost" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

if (-not (Test-Path $ToolsDir)) {
    throw "Decrypt tools not found: $ToolsDir"
}

Ensure-PoshSSH
$cred = New-RemoteCredential $RemoteUser $RemotePassword

Write-Host "[*] Connecting SSH..." -ForegroundColor Yellow
$session = New-SSHSession -ComputerName $RemoteHost -Credential $cred -Port $SshPort -AcceptKey
if (-not $session) {
    throw "SSH connection failed. Check host, username, password, and OpenSSH Server on remote PC."
}

try {
    Write-Host "[*] Preparing remote workspace..." -ForegroundColor Yellow
    Invoke-RemoteCommand $session "powershell -NoProfile -Command `"New-Item -ItemType Directory -Force -Path '$RemoteWorkRoot' | Out-Null`""

    Write-Host "[*] Uploading decrypt tools..." -ForegroundColor Yellow
    Upload-ToolsPackage $session $ToolsDir $RemoteToolsDir

    $remoteScriptLocal = Join-Path $PSScriptRoot "remote_wxwork_decrypt.ps1"
    Set-SCPItem -SessionId $session.SessionId -Path $remoteScriptLocal -Destination "$RemoteWorkRoot\remote_wxwork_decrypt.ps1" -AcceptKey

    Write-Host "[*] Extracting keys and decrypting on remote PC..." -ForegroundColor Yellow
    Write-Host "    (WeCom must be running on $RemoteHost)" -ForegroundColor DarkGray
    $output = Invoke-RemoteCommand $session @"
powershell -NoProfile -ExecutionPolicy Bypass -File "$RemoteWorkRoot\remote_wxwork_decrypt.ps1" -ToolsDir "$RemoteToolsDir" -OutputDir "$RemoteOutputDir"
"@

    $accountId = ""
    $accountRoot = ""
    foreach ($line in $output) {
        if ($line -match "account_id=(\d+)") {
            $accountId = $Matches[1]
        }
        if ($line -match "account_root=(.+) data_dir=") {
            $accountRoot = $Matches[1].Trim()
        }
        Write-Host "    $line" -ForegroundColor DarkGray
    }
    if (-not $accountId) {
        throw "Remote decrypt did not return account_id."
    }

    Write-Host "[*] Downloading decrypted databases..." -ForegroundColor Yellow
    $localTemp = Join-Path $env:TEMP ("wxwork_pull_{0}" -f ([guid]::NewGuid().ToString("N")))
    New-Item -ItemType Directory -Force -Path $localTemp | Out-Null
    Download-RemoteFolder $session $RemoteOutputDir $localTemp

    New-Item -ItemType Directory -Force -Path (Split-Path $LocalDecrypted -Parent) | Out-Null
    if (Test-Path $LocalDecrypted) {
        $backup = "$LocalDecrypted.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        Write-Host "    Backing up existing data to $backup" -ForegroundColor DarkGray
        Move-Item $LocalDecrypted $backup
    }
    $downloaded = Get-ChildItem $localTemp -Directory | Select-Object -First 1
    if ($downloaded -and (Test-Path (Join-Path $downloaded.FullName "message.db"))) {
        Move-Item $downloaded.FullName $LocalDecrypted
    } else {
        Move-Item (Join-Path $localTemp "*") $LocalDecrypted
    }
    Remove-Item $localTemp -Recurse -Force -ErrorAction SilentlyContinue

    if ($IncludeCache) {
        Write-Host "[*] Downloading attachment cache (may take a while)..." -ForegroundColor Yellow
        if (-not $accountRoot) {
            $accountRoot = "C:\Users\$RemoteUser\Documents\WXWork\$accountId"
        }
        $remoteCache = "$accountRoot\Cache"
        $cacheProbe = Invoke-SSHCommand -SessionId $session.SessionId -Command "powershell -NoProfile -Command `"if (Test-Path '$remoteCache') { 'yes' } else { 'no' }`"" -TimeOut 60
        if (($cacheProbe.Output -join "") -match "yes") {
            $localCacheRoot = Join-Path $LocalRemoteRoot $accountId
            Download-RemoteFolder $session $remoteCache (Join-Path $localCacheRoot "Cache")
        } else {
            Write-Host "    Cache folder not found at $remoteCache, skipping." -ForegroundColor Yellow
        }
    }

    $manifestSrc = Join-Path $LocalDecrypted "remote_manifest.json"
    if (Test-Path $manifestSrc) {
        New-Item -ItemType Directory -Force -Path $LocalRemoteRoot | Out-Null
        Copy-Item $manifestSrc (Join-Path $LocalRemoteRoot "manifest.json") -Force
    }

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host " Done! Data saved to:" -ForegroundColor Green
    Write-Host "   $LocalDecrypted" -ForegroundColor Green
    if ($IncludeCache) {
        Write-Host "   $LocalRemoteRoot\$accountId\Cache" -ForegroundColor Green
    }
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next: run .\start.ps1 and open http://localhost:5173" -ForegroundColor Cyan
}
finally {
    if ($session) {
        Remove-SSHSession -SessionId $session.SessionId | Out-Null
    }
}
