@echo off
rem Interactive hunt: asks keyword / output dir / file count
cd /d %~dp0..
set "KW="
set /p KW=Keyword:
if "%KW%"=="" (
    echo No keyword, exit.
    pause
    exit /b
)
set "OUT="
set /p OUT=Output dir ^(Enter = default sync folder^):
set "N="
set /p N=Max files ^(Enter = 20^):
if "%N%"=="" set N=20
if "%OUT%"=="" (
    python -m studycrawler hunt "%KW%" --files %N%
) else (
    python -m studycrawler hunt "%KW%" --files %N% --out "%OUT%"
)
echo.
pause
