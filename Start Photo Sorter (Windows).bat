@echo off
cd /d "%~dp0"
if exist .git ( where git >nul 2>nul && git pull --ff-only --quiet )
where py >nul 2>nul && ( py -3 -m photosorter ) || ( python -m photosorter )
pause
