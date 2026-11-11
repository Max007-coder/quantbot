@echo off
rem ── the bot's settings live here ────────────────────────────────────────
rem   --live                              also run the LIVE engine (port 8082)
rem   --risk 0.25                         percent of equity risked per trade
rem   --live-equity 100                   live sizing baseline
rem
rem DANGER: this line starts the live engine at every login. While
rem QB_BINANCE_TESTNET=true in .env nothing real is traded. The moment you set
rem that to false, this autostart places real orders unattended after every
rem reboot. Run run\live_preflight.py before you flip it.
rem   --symbols BTC/USDT,ETH/USDT         pairs to watch
rem   --timeframe 5m                      bar size
rem   --risk 0.5                          percent of equity risked per trade
rem   --paper-venues binance,mt5          paper platforms, switchable per trade
rem   --paper-equity 10000                starting virtual equity
rem Edit this line, then: run\stop_bot.bat  and  run\start_bot.bat
cd /d "%~dp0.."
if not exist logs mkdir logs
rem never run a second copy: two engines on one paper book would double-trade it
netstat -ano | findstr /i listening | findstr /c:":8080 " >nul
if not errorlevel 1 (
  rem its own file: the running copy holds duo.out open and would refuse the write
  echo %DATE% %TIME% skipped, already running >> "%~dp0..\logs\bot-skip.log"
  exit /b 0
)
echo. >> logs\duo.out
echo ==== starting %DATE% %TIME% ==== >> logs\duo.out
.venv\Scripts\python.exe duo.py --live --symbols BTC/USDT,ETH/USDT --timeframe 5m --risk 0.25 --paper-venues binance,mt5 --paper-equity 10000 --live-venues binance --live-equity 100 >> logs\duo.out 2>&1
echo ==== exited %DATE% %TIME% code %ERRORLEVEL% ==== >> logs\duo.out
