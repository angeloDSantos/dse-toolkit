@echo off
echo.
echo  ======================================
echo   GDS TOOLKIT — SETUP
echo  ======================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed or not in PATH.
    echo  Download from https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist "venv" (
    echo  Creating virtual environment...
    python -m venv venv
    echo  Done.
) else (
    echo  Virtual environment already exists.
)

echo  Installing dependencies...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
echo  Done.

echo.
echo  ======================================
echo   SETUP COMPLETE
echo  ======================================
echo.
echo   To launch:  launch.bat
echo.
pause
