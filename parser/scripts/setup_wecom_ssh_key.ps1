# Run once on local PC. Enter SSH password when prompted.
param(
    [string]$RemoteHost = "192.168.2.14",
    [string]$RemoteUser = "mache"
)

$ErrorActionPreference = "Stop"
$ssh = "$env:WINDIR\System32\OpenSSH\ssh.exe"
$keygen = "$env:WINDIR\System32\OpenSSH\ssh-keygen.exe"
$keyPath = Join-Path $env:USERPROFILE ".ssh\wecom_pull"

Write-Host "=== Setup SSH key for WeCom remote pull ===" -ForegroundColor Cyan

if (-not (Test-Path (Split-Path $keyPath))) {
    New-Item -ItemType Directory -Force -Path (Split-Path $keyPath) | Out-Null
}

if (-not (Test-Path $keyPath)) {
    Write-Host "Generating SSH key: $keyPath" -ForegroundColor Yellow
    & $keygen -t ed25519 -f $keyPath -N '""' -C "wecom-pull"
}

$pub = (Get-Content "$keyPath.pub" -Raw).Trim().Replace("'", "''")

$remoteScript = @"
`$pub = '$pub'
`$marker = 'wecom-pull'

function Set-KeyFile([string]`$path) {
    `$dir = Split-Path `$path -Parent
    if (-not (Test-Path `$dir)) {
        New-Item -ItemType Directory -Force -Path `$dir | Out-Null
    }
    if ((Test-Path `$path) -and (Select-String -Path `$path -Pattern `$marker -Quiet)) {
        Write-Output "exists: `$path"
        return
    }
    Add-Content -Path `$path -Value `$pub -Encoding ascii
    Write-Output "added: `$path"
    icacls `$dir /inheritance:r /grant "`${env:USERNAME}:(F)" /grant "SYSTEM:(F)" | Out-Null
    icacls `$path /inheritance:r /grant "`${env:USERNAME}:(F)" /grant "SYSTEM:(F)" | Out-Null
}

`$userFile = Join-Path `$env:USERPROFILE '.ssh\authorized_keys'
Set-KeyFile `$userFile

`$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (`$isAdmin) {
    `$adminFile = 'C:\ProgramData\ssh\administrators_authorized_keys'
    Set-KeyFile `$adminFile
    icacls `$adminFile /inheritance:r /grant "Administrators:(F)" /grant "SYSTEM:(F)" | Out-Null
}

Write-Output 'KEY_INSTALL_OK'
"@

$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remoteScript))

Write-Host ""
Write-Host "Enter SSH password for ${RemoteUser}@${RemoteHost} (only needed once):" -ForegroundColor Yellow
& $ssh -o StrictHostKeyChecking=accept-new "${RemoteUser}@${RemoteHost}" "powershell -NoProfile -EncodedCommand $encoded"

Write-Host ""
Write-Host "Testing key login..." -ForegroundColor Yellow
& $ssh -i $keyPath -o BatchMode=yes -o IdentitiesOnly=yes "${RemoteUser}@${RemoteHost}" "whoami"
if ($LASTEXITCODE -eq 0) {
    Write-Host "SSH key setup complete." -ForegroundColor Green
    Write-Host "You can now run: python scripts\pull_wecom_remote.py --include-cache" -ForegroundColor Green
} else {
    Write-Host "Key login failed." -ForegroundColor Red
    Write-Host "If mache is an administrator, on remote PC run as admin:" -ForegroundColor Yellow
    Write-Host "  notepad C:\ProgramData\ssh\administrators_authorized_keys" -ForegroundColor Yellow
    Write-Host "Paste this line, save, then: Restart-Service sshd" -ForegroundColor Yellow
    Write-Host ""
    Write-Host $pub -ForegroundColor Cyan
    exit 1
}
