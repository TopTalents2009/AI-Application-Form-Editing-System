# Run this ON the remote PC (192.168.2.14) as Administrator.
# Enables OpenSSH Server and firewall rules so your local PC can pull WeCom data.

$ErrorActionPreference = "Stop"

Write-Host "=== Enable OpenSSH Server ===" -ForegroundColor Cyan

$cap = Get-WindowsCapability -Online | Where-Object { $_.Name -like "OpenSSH.Server*" }
if ($cap.State -ne "Installed") {
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
}

Start-Service sshd -ErrorAction SilentlyContinue
Set-Service -Name sshd -StartupType Automatic

# sshd should use ProgramData config
$sshdConfig = "$env:ProgramData\ssh\sshd_config"
if (Test-Path $sshdConfig) {
    $text = Get-Content $sshdConfig -Raw
    if ($text -notmatch "(?m)^PasswordAuthentication\s+yes") {
        Add-Content $sshdConfig "`nPasswordAuthentication yes"
    }
}

Restart-Service sshd

Write-Host "=== Firewall rules ===" -ForegroundColor Cyan
New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" `
    -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -ErrorAction SilentlyContinue | Out-Null

# Allow ping for troubleshooting (optional)
New-NetFirewallRule -Name "ICMPv4-Allow-Ping" -DisplayName "ICMP Allow Ping" `
    -Enabled True -Direction Inbound -Protocol ICMPv4 -IcmpType 8 -Action Allow -ErrorAction SilentlyContinue | Out-Null

Write-Host "=== Status ===" -ForegroundColor Cyan
Get-Service sshd | Format-Table Name, Status, StartType
Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue |
    Select-Object DisplayName, Enabled, Direction, Action

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -like "192.168.*" -and $_.PrefixOrigin -ne "WellKnown"
} | Select-Object -First 1).IPAddress

Write-Host ""
Write-Host "Done. This PC IP: $ip" -ForegroundColor Green
Write-Host "On your local PC, test:  ping $ip" -ForegroundColor Green
Write-Host "Then:  ssh `"che3 ma`"@$ip" -ForegroundColor Green
