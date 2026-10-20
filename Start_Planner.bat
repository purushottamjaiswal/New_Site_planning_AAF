@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto setup
".venv\Scripts\python.exe" check_desktop_environment.py >nul 2>&1
if errorlevel 1 goto setup
goto run
:setup
call Setup_Desktop_Environment.bat --no-pause
if errorlevel 1 goto failed
:run
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Could not start. Read the error above and the setup section in README.md.
echo If Python is managed by IT, use its approved package mirror or Anaconda environment.
pause
exit /b 1
