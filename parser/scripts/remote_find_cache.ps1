# Find one WeCom cache file by name or md5. Prints the full path.
param(
    [Parameter(Mandatory = $true)][string]$AccountRoot,
    [string]$Name = "",
    [string]$Md5 = ""
)

$ErrorActionPreference = "SilentlyContinue"
$roots = @("Image", "File", "Voice") | ForEach-Object { Join-Path $AccountRoot "Cache\$_" }

function Find-InRoot([string]$Root, [string]$Filter) {
    if (-not $Filter) { return $null }
    if (-not (Test-Path $Root)) { return $null }
    return Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $Filter |
        Select-Object -First 1
}

foreach ($root in $roots) {
    if ($Name) {
        $hit = Find-InRoot $root $Name
        if ($hit) { Write-Output $hit.FullName; exit 0 }
        $hit = Find-InRoot $root "*$Name*"
        if ($hit) { Write-Output $hit.FullName; exit 0 }
    }
    if ($Md5) {
        $hit = Find-InRoot $root "*$Md5*"
        if ($hit) { Write-Output $hit.FullName; exit 0 }
    }
}
exit 0
