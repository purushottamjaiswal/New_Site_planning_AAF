@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First install: creating the Python environment...
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import numpy,pandas,scipy,shapely,pyproj,pyogrio,ijson,rasterio" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Could not start. Read the error above and the setup section in README.md.
echo If Python is managed by IT, use its approved package mirror or Anaconda environment.
pause
exit /b 1
