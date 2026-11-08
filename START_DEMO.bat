@echo off
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 goto no_python

py -3 -m pip install -r requirements.txt
if errorlevel 1 goto setup_failed

echo.
echo Interview Mate is starting.
echo Open http://127.0.0.1:5000/ in your browser.
echo Dashboard visual preview: http://127.0.0.1:5000/dashboard-preview.html
echo Keep this window open while you use the website.
echo.
py -3 app.py
pause
exit /b 0

:no_python
echo Python 3 was not found. Install Python 3.10 or newer, then run this file again.
pause
exit /b 1

:setup_failed
echo The app dependencies could not be installed. Check your internet connection and try again.
pause
exit /b 1
