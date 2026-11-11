@echo off
setlocal
cd /d "%~dp0.."
echo.
for %%P in (8080 8081 8082) do (
  netstat -ano | findstr /i listening | findstr /c:":%%P " >nul
  if errorlevel 1 (echo    :%%P  stopped) else (echo    :%%P  running)
)
echo.
echo    dashboard  http://127.0.0.1:8080
echo    log        logs\duo.out
if exist logs\duo.out (
  echo.
  echo    last log lines:
  powershell -NoProfile -Command "Get-Content -Path 'logs\duo.out' -Tail 8"
)
if "%~1"=="" pause
