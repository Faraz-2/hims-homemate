@echo off
rem Home Mate HIMS launcher for the Windows server laptop.
rem Layout: backend\ (Python code), frontend\ (pages and styling), instance\ (your data, created on first start).
cd /d "%~dp0"
if not exist venv\Scripts\activate.bat goto setup
goto run

:setup
echo First run: creating the Python environment...
python -m venv venv
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
goto start

:run
call venv\Scripts\activate.bat

:start
cd backend
python run.py
pause
