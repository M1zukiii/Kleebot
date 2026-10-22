@echo off
setlocal
cd /d "%~dp0"
call start-local.cmd --maintenance-configure %*
exit /b %errorlevel%
