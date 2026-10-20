@echo off
cd /d "%~dp0"
echo This creates a separate environment for the planning engine. QGIS Python is not modified.
py -3 setup_environment.py
if errorlevel 1 echo Setup failed. See the error above or use your IT-approved Python environment and package mirror.
pause
