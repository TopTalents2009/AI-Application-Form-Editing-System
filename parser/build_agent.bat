@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 打包企业微信同步助手 WeComSyncAgent.exe ===
if exist "agent\VERSION" (
  echo 版本:
  type "agent\VERSION"
)
python -m pip install -q pyinstaller pycryptodome
python -m PyInstaller --noconfirm --clean "agent\wecom_sync_agent.spec"
if errorlevel 1 (
  echo 打包失败
  pause
  exit /b 1
)
echo.
echo 完成: dist\WeComSyncAgent.exe
echo 把这个 exe 拷到远端电脑运行即可。
pause
