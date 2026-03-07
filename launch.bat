@echo off

:: Run setup if venv doesn't exist yet
if not exist "venv" (
    echo  First run detected — running setup...
    call setup.bat
)

:: Activate and install flask if needed
call venv\Scripts\activate.bat
python -m pip install flask --quiet 2>nul

echo.
echo  ================================================================
echo     DSE TOOLKIT — Starting Web Dashboard
echo  ================================================================
echo.
echo   Opening http://localhost:5000 in your browser...
echo   Press Ctrl+C to stop the server.
echo.

:: Open browser after a short delay
start "" "http://localhost:5000"

:: Start Flask
python app.py
