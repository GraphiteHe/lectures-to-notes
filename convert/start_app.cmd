@echo off
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
where pythonw.exe >nul 2>nul
if not errorlevel 1 (
  start "" pythonw.exe "%~dp0app\lecture_app.pyw"
  exit /b
)
python "%~dp0app\lecture_app.pyw"
if errorlevel 1 pause
