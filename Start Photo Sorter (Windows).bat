@echo off
cd /d "%~dp0"
set GIT_TERMINAL_PROMPT=0
if exist .git ( where git >nul 2>nul && git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 pull --ff-only --quiet || echo Could not check for updates; starting the version on this computer. )
where py >nul 2>nul && ( py -3 -m photosorter ) || ( python -m photosorter )
pause
