@echo off
net session >nul 2>&1
if errorlevel 1 (
  echo Requesting Administrator to delete portproxy 0.0.0.0:3777 ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
netsh interface portproxy delete v4tov4 listenport=3777 listenaddress=0.0.0.0
echo.
echo Removed 0.0.0.0:3777 portproxy.
echo Close this window, then run start.cmd again.
echo.
pause
