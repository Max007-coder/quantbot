@echo off
setlocal
cd /d "%~dp0.."
echo stopping quantbot ...
for %%P in (8080 8081 8082) do (
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr /i listening ^| findstr /c:":%%P "') do (
    taskkill /F /T /PID %%a >nul 2>&1
  )
)
echo stopped.
if "%~1"=="" pause
