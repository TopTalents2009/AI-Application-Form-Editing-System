# Start development servers (alias of project root start.ps1)
$ProjectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $ProjectRoot "start.ps1")
