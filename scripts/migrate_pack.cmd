@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "PYEXE=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
"%PYEXE%" "%~dp0migrate.py" pack %*
echo.
pause
