@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" setup_desktop_environment.py
) else (
  py -3 setup_desktop_environment.py
)
if errorlevel 1 goto failed
echo Complete desktop environment is ready, including rasterio.
if not "%~1"=="--no-pause" pause
exit /b 0
:failed
echo Desktop setup failed. Read the error above. Use your IT-approved package mirror if required.
if not "%~1"=="--no-pause" pause
exit /b 1
