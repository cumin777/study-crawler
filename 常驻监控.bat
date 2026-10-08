@echo off
rem Start watch mode: sources + keywords.txt, runs until Ctrl+C
cd /d %~dp0
python -m studycrawler watch
echo.
pause
