@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "PYEXE=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
if "%~1"=="" (
  echo Usage: migrate_restore.cmd --from 迁移包目录 [--dest 新目录]
  echo Example: migrate_restore.cmd --from D:\shenbaoshu-migrate-20261021-1400 --dest D:\work\agent修改申报书_py
  pause
  exit /b 2
)
"%PYEXE%" "%~dp0migrate.py" restore %*
echo.
pause
