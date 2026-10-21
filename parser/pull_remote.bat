@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo  SSH 远程解密企业微信（数据留在远端）
echo ========================================
echo 默认不拉回本机。打开网页后按需读取聊天和文件。
echo 远端需: 1) 已开 OpenSSH   2) 企业微信已登录
echo 整包下载请加参数: --download
echo.
python "%~dp0scripts\pull_wecom_remote.py" %*
if errorlevel 1 pause
