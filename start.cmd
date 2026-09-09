@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0run.py" (
  echo ERROR: run.py not found in %~dp0
  pause
  exit /b 1
)
chcp 65001 >nul
set PYTHONUTF8=1
title shenbaoshu-fastapi
set "PYEXE="
where conda >nul 2>nul && call conda activate work >nul 2>nul
if "%CONDA_DEFAULT_ENV%"=="work" set "PYEXE=python"
if not defined PYEXE set "PYEXE=C:\Users\1\miniconda3\envs\work\python.exe"
echo interpreter: %PYEXE%

REM only stop python/uvicorn on 3777/3778, never svchost
powershell -NoProfile -Command "$ports=3777,3778; foreach($port in $ports){ $cs=@(Get-NetTCPConnection -LocalPort $port -State Listen -EA SilentlyContinue); foreach($c in $cs){ try{ $p=Get-Process -Id $c.OwningProcess -EA Stop; if($p.ProcessName -match 'python|uvicorn'){ Stop-Process -Id $p.Id -Force; Write-Host ('stopped old python pid='+$p.Id+' port='+$port) } } catch {} } }"

set SHENBAOSHU_PORT=3777
"%PYEXE%" "%~dp0run.py" --probe
if errorlevel 1 (
  set SHENBAOSHU_PORT=3778
  echo.
  echo [WARN] 3777 is held by Windows portproxy / svchost
  echo        using http://127.0.0.1:3778
  echo        to free 3777, right-click fix_portproxy.cmd and Run as administrator
  echo.
)

echo ============================================
echo   shenbaoshu editor  FastAPI
echo   http://127.0.0.1:%SHENBAOSHU_PORT%    Ctrl+C to stop
echo ============================================

start "" /min powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{$null=Get-NetTCPConnection -LocalPort %SHENBAOSHU_PORT% -State Listen -EA Stop;break}catch{Start-Sleep -Milliseconds 500}}; Start-Process 'http://127.0.0.1:%SHENBAOSHU_PORT%'"
:loop
"%PYEXE%" "%~dp0run.py"
echo [%date% %time%] server exited, restart in 3s
timeout /t 3 /nobreak >nul
goto loop
