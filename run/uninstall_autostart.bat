@echo off
setlocal
set "VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\quantbot.vbs"
if exist "%VBS%" (
  del "%VBS%"
  echo removed: %VBS%
) else (
  echo autostart was not installed
)
if "%~1"=="" pause
