@echo off
rem One-shot crawl of all enabled sources
cd /d %~dp0..
python -m studycrawler crawl
echo.
pause
