@echo off

:: Run setup if venv doesn't exist yet
if not exist "venv" (
    echo  First run detected — running setup...
    call setup.bat
)

:: Activate and run
call venv\Scripts\activate.bat
python scraper.py
pause
