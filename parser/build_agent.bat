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
  echo 打包助手失败
  pause
  exit /b 1
)
echo.
echo === 打包卸载程序 WeComSyncUninstall.exe ===
python -m PyInstaller --noconfirm --clean "agent\wecom_sync_uninstall.spec"
if errorlevel 1 (
  echo 打包卸载程序失败
  pause
  exit /b 1
)
echo.
echo 完成: dist\WeComSyncAgent.exe
echo 完成: dist\WeComSyncUninstall.exe
echo 把助手 exe 拷到远端电脑运行；卸掉时把卸载程序发到同一台电脑运行。
pause
