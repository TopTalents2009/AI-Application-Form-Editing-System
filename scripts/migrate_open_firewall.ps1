# 新机以管理员运行：放行解析服务 TCP 8767，并打印局域网 IP。
$ErrorActionPreference = 'Stop'
$name = 'shenbaoshu-parser-8767'
try {
    $rule = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
} catch {
    $rule = $null
}
if (-not $rule) {
    try {
        New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort 8767 -Action Allow | Out-Null
        Write-Host "added inbound TCP 8767 ($name)"
    } catch {
        Write-Host "firewall rule failed, run PowerShell as Administrator:"
        Write-Host $_.Exception.Message
        Write-Host "netsh advfirewall firewall add rule name=$name dir=in action=allow protocol=TCP localport=8767"
        exit 1
    }
} else {
    Write-Host "firewall rule already exists: $name"
}
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
    ForEach-Object { Write-Host ("LAN " + $_.IPAddress) }
Write-Host "Point each WeCom helper to http://<LAN-IP>:8767"
exit 0
