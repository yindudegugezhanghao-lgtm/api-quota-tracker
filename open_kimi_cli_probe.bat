@echo off
setlocal
chcp 65001 >nul 2>nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 kimi_cli_probe.py
) else (
  python kimi_cli_probe.py
)
if errorlevel 1 pause
