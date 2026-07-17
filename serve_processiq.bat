@echo off
REM Headless ProcessIQ server (used by the "ProcessIQ Server" scheduled task).
REM Serves UI + API on http://127.0.0.1:8100 without opening a browser.
cd /d "%~dp0"
.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8100
