@echo off
setlocal
cd /d "%~dp0."
if not exist "%~dp0run.py" (
  echo ERROR: run.py not found in %~dp0
  pause
  exit /b 1
)
chcp 65001 >nul
set PYTHONUTF8=1
title shenbaoshu-fastapi
echo ============================================
echo   shenbaoshu editor  FastAPI
echo   http://127.0.0.1:3777    Ctrl+C to stop
echo ============================================
set "PYEXE="
where conda >nul 2>nul && call conda activate work >nul 2>nul
if "%CONDA_DEFAULT_ENV%"=="work" set "PYEXE=python"
if not defined PYEXE set "PYEXE=C:\Users\1\miniconda3\envs\work\python.exe"
echo interpreter: %PYEXE%
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 3777 -State Listen -ErrorAction SilentlyContinue;if($c){Stop-Process -Id $c.OwningProcess -Force;Write-Host 'stopped old instance'}"
start "" /min powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{$c=Get-NetTCPConnection -LocalPort 3777 -State Listen -ErrorAction Stop;break}catch{Start-Sleep -Milliseconds 500}};Start-Process 'http://127.0.0.1:3777'"
:loop
"%PYEXE%" "%~dp0run.py"
echo [%date% %time%] server exited, restart in 3s
timeout /t 3 /nobreak >nul
goto loop
