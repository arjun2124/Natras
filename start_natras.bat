@echo off
title NAT RAS Automated Trading & Data Collector
cd /d "%~dp0"
echo ================================================================
echo               NAT RAS PLATFORM AUTOSTART LAUNCHER
echo ================================================================
echo Checking environment and starting NAT RAS Autostart...
python -m natras.cli.autostart
pause
