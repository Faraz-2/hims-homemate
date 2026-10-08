@echo off
rem Complete backup: full backup + one zip per department + one zip per person, filed by date.
rem Schedule this daily with Task Scheduler (HIMS also does it by itself at the time set in System settings
rem while it is running; running both is harmless).
cd /d "%~dp0"
call venv\Scripts\activate.bat
cd backend
flask --app run backup-complete
