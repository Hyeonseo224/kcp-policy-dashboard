@echo off
cd /d "%~dp0"
echo [1/2] Installing Python packages...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation failed. Check the error above.
  pause
  exit /b 1
)
echo [2/2] Starting KCP dashboard...
start "" http://127.0.0.1:5000
python app.py
pause
