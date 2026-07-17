@echo off
REM One-click ProcessIQ launcher — serves UI + API on http://localhost:8000
cd /d "%~dp0"
echo Starting ProcessIQ at http://localhost:8000 ...
start "" http://localhost:8000
.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
