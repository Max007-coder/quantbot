@echo off
cd /d "%~dp0.."
echo Starting quantbot with the LIVE panel enabled.
echo Keys and tokens come from .env - nothing secret is stored in this file.
python start.py --live
pause
