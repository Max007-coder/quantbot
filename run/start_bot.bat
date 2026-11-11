@echo off
setlocal
cd /d "%~dp0.."
netstat -ano | findstr /i listening | findstr /c:":8080 " >nul
if not errorlevel 1 (
  echo quantbot is already running  -  http://127.0.0.1:8080
  start "" "http://127.0.0.1:8080"
  goto done
)
echo starting quantbot in the background, no window ...
wscript //nologo "%~dp0hidden.vbs"
rem powershell, not timeout: this machine has a "timeout" on PATH that is not the Windows one
powershell -NoProfile -Command "Start-Sleep -Seconds 8"
netstat -ano | findstr /i listening | findstr /c:":8080 " >nul
if errorlevel 1 (
  echo it did not come up - check logs\duo.out
) else (
  echo running  -  http://127.0.0.1:8080
  start "" "http://127.0.0.1:8080"
)
:done
if "%~1"=="" pause
