# Allow inbound TCP for the ChatInsight web UI and API.
# Run as Administrator once if start.ps1 could not add the rules.
# Saved as UTF-8 with BOM so Windows PowerShell 5.1 can parse it.
param(
    [int[]]$Ports = @(5173, 8767)
)

$ErrorActionPreference = "Stop"

function Ensure-ChatInsightFirewallRule([int]$Port) {
    $name = "ChatInsight TCP $Port"
    $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Set-NetFirewallRule -DisplayName $name -Enabled True -Profile Any -ErrorAction SilentlyContinue | Out-Null
        Write-Host "Rule exists: $name" -ForegroundColor Green
        return
    }
    New-NetFirewallRule `
        -DisplayName $name `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Any `
        -ErrorAction Stop | Out-Null
    Write-Host "Opened: $name" -ForegroundColor Green
}

foreach ($port in $Ports) {
    Ensure-ChatInsightFirewallRule $port
}

$portText = ($Ports | ForEach-Object { [string]$_ }) -join ", "
Write-Host ""
Write-Host ("LAN browsers can use this PC IP. TCP ports: " + $portText) -ForegroundColor Cyan
