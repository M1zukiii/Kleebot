@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 scripts\local_test.py %*
goto done
:usepython
where python >nul 2>nul
if errorlevel 1 goto missing
python scripts\local_test.py %*
goto done
:missing
echo Install Python 3.12 or newer: https://www.python.org/downloads/windows/
echo Enable the Python launcher or Add Python to PATH during installation.
pause
exit /b 1
:done
if errorlevel 1 pause
