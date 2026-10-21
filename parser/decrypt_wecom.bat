@echo off
chcp 65001 >nul
cd /d "%~dp0"
python "%~dp0scripts\decrypt_wecom.py" %*
if errorlevel 1 pause
