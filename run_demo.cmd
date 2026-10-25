@echo off
rem Replays the recorded demo match from demo\ - no Azure account or keys needed.
rem Extra options pass through, e.g. run_demo.cmd --profile casual_ko --speed 2
cd /d "%~dp0"
python scripts\run_match.py --replay %*
if errorlevel 1 pause
