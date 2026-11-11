@echo off
cd /d "%~dp0.."
echo Starting quantbot in PAPER mode - you will be asked which platform to use.
python start.py --paper
pause
