# 企业微信一键解密（调用 Python 脚本）
param(
    [switch]$SkipKeys,
    [switch]$ScanBareHex
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$PyScript = Join-Path $PSScriptRoot "decrypt_wecom.py"
$Args = @($PyScript)

if ($SkipKeys) { $Args += "--skip-keys" }
if ($ScanBareHex) { $Args += "--scan-bare-hex" }

Push-Location $ProjectRoot
try {
    & python @Args
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
