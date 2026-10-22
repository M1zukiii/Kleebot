@echo off
setlocal
cd /d "%~dp0"
call start-local.cmd --maintain %*
exit /b %errorlevel%
