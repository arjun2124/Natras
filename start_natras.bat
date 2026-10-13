@echo off
title NAT RAS Automated Trading Platform (Option A)
cd /d "%~dp0"
echo ================================================================
echo               NAT RAS PLATFORM LAUNCHER (OPTION A)
echo ================================================================
echo Starting NAT RAS Autostart Engine...
python -m natras.cli.autostart %*
pause

