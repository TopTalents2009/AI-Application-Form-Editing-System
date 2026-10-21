# Pack WeCom Cache folders on the remote PC for SFTP download.
param(
    [Parameter(Mandatory = $true)][string]$AccountRoot,
    [Parameter(Mandatory = $true)][string]$OutputTar,
    [switch]$FullCache
)

$ErrorActionPreference = "Stop"
$cacheRoot = Join-Path $AccountRoot "Cache"
if (-not (Test-Path $cacheRoot)) {
    throw "Cache folder not found: $cacheRoot"
}

if (Test-Path $OutputTar) {
    Remove-Item $OutputTar -Force
}

$tar = Get-Command tar.exe -ErrorAction SilentlyContinue
if (-not $tar) {
    throw "tar.exe not found. Windows 10 1803+ is required."
}

Push-Location $cacheRoot
try {
    if ($FullCache) {
        & tar.exe -cf $OutputTar .
    } else {
        $items = @()
        foreach ($name in @("Image", "File", "Voice")) {
            if (Test-Path (Join-Path $cacheRoot $name)) {
                $items += $name
            }
        }
        if (-not $items) {
            throw "Cache Image/File/Voice folders not found under $cacheRoot"
        }
        & tar.exe -cf $OutputTar -- @items
    }
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$size = (Get-Item $OutputTar).Length
Write-Output "OK cache_tar=$OutputTar size=$size"
