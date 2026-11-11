@echo off
cd /d "%~dp0.."
echo Market advisor - settings and Gmail credentials come from .env
python advisor.py --symbols BTC/USDT,ETH/USDT --timeframe 15m --every 15
pause
