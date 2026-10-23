@echo off
cd /d "%~dp0"
start "" http://localhost:8000
.venv\Scripts\python.exe -m uvicorn app.server:app --port 8000
