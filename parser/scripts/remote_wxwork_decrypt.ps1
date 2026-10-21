# Runs on the remote Windows PC: extract WXWork keys and decrypt databases.
param(
    [Parameter(Mandatory = $true)][string]$ToolsDir,
    [Parameter(Mandatory = $true)][string]$OutputDir
)

$ErrorActionPreference = "Stop"

function Get-WxWorkDataDir {
    $candidates = @()

    try {
        $custom = (Get-ItemProperty -Path "HKCU:\Software\Tencent\WXWork" -Name DataLocationPath -ErrorAction Stop).DataLocationPath
        if ($custom -and (Test-Path $custom)) {
            foreach ($uid in Get-ChildItem -Path $custom -Directory -ErrorAction SilentlyContinue) {
                $dataDir = Join-Path $uid.FullName "Data"
                if ((Test-Path (Join-Path $dataDir "message.db"))) {
                    $candidates += $dataDir
                }
            }
        }
    } catch {
        # ignore
    }

    $docs = Join-Path $env:USERPROFILE "Documents\WXWork"
    if (Test-Path $docs) {
        foreach ($uid in Get-ChildItem -Path $docs -Directory -ErrorAction SilentlyContinue) {
            $dataDir = Join-Path $uid.FullName "Data"
            if ((Test-Path (Join-Path $dataDir "message.db"))) {
                if ($candidates -notcontains $dataDir) {
                    $candidates += $dataDir
                }
            }
            foreach ($ver in Get-ChildItem -Path $uid.FullName -Directory -ErrorAction SilentlyContinue) {
                $dataDir = Join-Path $ver.FullName "Data"
                if ((Test-Path (Join-Path $dataDir "message.db"))) {
                    if ($candidates -notcontains $dataDir) {
                        $candidates += $dataDir
                    }
                }
            }
        }
    }

    if (-not $candidates) {
        throw "WXWork Data directory not found. Is WeCom installed on this PC?"
    }

    $candidates = $candidates | Sort-Object { (Get-Item $_).LastWriteTime } -Descending
    return $candidates[0]
}

function Get-PythonExe {
    $patterns = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
    )
    foreach ($path in $patterns) {
        if (Test-Path $path) {
            return $path
        }
    }

    $pyLauncher = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($pyLauncher) {
        $resolved = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
        if ($resolved -and (Test-Path $resolved)) {
            return $resolved.Trim()
        }
    }

    foreach ($line in (& where.exe python 2>$null)) {
        $line = $line.Trim()
        if ($line -and $line -notlike "*WindowsApps*") {
            return $line
        }
    }
    return $null
}

function Ensure-Python {
    $python = Get-PythonExe
    if ($python) {
        return $python
    }

    Write-Host "Python not found. Installing Python 3.12..."
    $installerDir = "C:\wxwork_pull"
    New-Item -ItemType Directory -Force -Path $installerDir | Out-Null
    $installer = Join-Path $installerDir "python-3.12.7-amd64.exe"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe" -OutFile $installer
    Start-Process -FilePath $installer -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1" -Wait

    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [System.Environment]::GetEnvironmentVariable("Path", "User")

    $python = Get-PythonExe
    if (-not $python) {
        throw "Python install finished but python.exe was not found."
    }
    return $python
}

function Ensure-PythonDeps([string]$PythonExe) {
    & $PythonExe -m pip install --disable-pip-version-check -q pycryptodome 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install pycryptodome on remote PC."
    }
}

function Get-WxWorkAccountRoot([string]$DataDir) {
    $current = (Resolve-Path $DataDir).Path
    while ($current) {
        $parent = Split-Path $current -Parent
        if (-not $parent -or $parent -eq $current) {
            break
        }
        if ((Split-Path $current -Leaf) -eq "Data") {
            return Split-Path $current -Parent
        }
        $current = $parent
    }
    return ""
}

$python = Ensure-Python
Ensure-PythonDeps $python

$wx = Get-Process WXWork -ErrorAction SilentlyContinue
if (-not $wx) {
    throw "WXWork.exe is not running. Start WeCom on this PC, then retry."
}

$dataDir = Get-WxWorkDataDir
$accountRoot = Get-WxWorkAccountRoot $dataDir
$accountId = Split-Path $accountRoot -Leaf

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$configPath = Join-Path $ToolsDir "config.json"
$config = @{
    wxwork_db_dir = $dataDir
    wxwork_keys_file = "wxwork_keys.json"
    wxwork_decrypted_dir = $OutputDir
} | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($configPath, $config, [System.Text.UTF8Encoding]::new($false))

$env:WECHAT_DECRYPT_NONINTERACTIVE = "1"
Push-Location $ToolsDir
& $python find_wxwork_keys.py
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    throw "find_wxwork_keys.py failed with exit code $LASTEXITCODE"
}

& $python decrypt_wxwork_db.py
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    throw "decrypt_wxwork_db.py failed with exit code $LASTEXITCODE"
}
Pop-Location

$msgDb = Join-Path $OutputDir "message.db"
if (-not (Test-Path $msgDb)) {
    throw "Decryption finished but message.db was not created."
}

$header = [System.IO.File]::ReadAllBytes($msgDb)[0..15]
$plain = [System.Text.Encoding]::ASCII.GetString($header)
if ($plain -ne "SQLite format 3`0") {
    throw "message.db is still encrypted. Key extraction may have failed."
}

$manifest = @{
    data_dir = $dataDir
    account_root = $accountRoot
    account_id = $accountId
    decrypted_dir = $OutputDir
    host = $env:COMPUTERNAME
    pulled_at = (Get-Date).ToString("o")
}
$manifestPath = Join-Path $OutputDir "remote_manifest.json"
Set-Content -Path $manifestPath -Value ($manifest | ConvertTo-Json -Depth 4) -Encoding UTF8

Write-Output "OK account_id=$accountId account_root=$accountRoot data_dir=$dataDir"
