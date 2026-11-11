@echo off
setlocal
cd /d "%~dp0.."
set "VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\quantbot.vbs"
>  "%VBS%" echo ' starts quantbot hidden at login
>> "%VBS%" echo Set sh = CreateObject("WScript.Shell")
>> "%VBS%" echo sh.Run "wscript.exe ""%CD%\run\hidden.vbs""", 0, False
echo installed: %VBS%
echo quantbot now starts hidden every time you log in.
if "%~1"=="" pause
