@echo off
cd /d "%~dp0"
title Datazoic Agentic System Launcher

echo ============================================================================
echo   Datazoic Agentic System (50 to 1,000+ Tools)
echo   Local Setup and Launcher
echo ============================================================================
echo.

:: 1. Check Python installation
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH. Please install Python 3.10 or higher.
    pause
    exit /b 1
)

:: 2. Check or create virtual environment
if exist ".venv\Scripts\activate.bat" goto VENV_EXISTS

echo [*] Creating isolated virtual environment inside .venv ...
python -m venv .venv
if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
)
echo [*] Virtual environment created successfully.

:VENV_EXISTS
echo [*] Activating virtual environment...
call .venv\Scripts\activate.bat

:: 3. Ensure .env exists
if not exist ".env" (
    echo [*] Creating .env from .env.example...
    copy .env.example .env >nul
)

:: 4. Install dependencies
echo [*] Installing requirements from requirements.txt...
python -m pip install -r requirements.txt

echo.
echo ============================================================================
echo   Starting Services:
echo   - Backend API:  http://127.0.0.1:8000
echo   - Frontend UI:  http://localhost:8501
echo   - Credentials:  admin@examplemail.com / admin@123
echo ============================================================================
echo.

:: 5. Launch FastAPI backend in a separate dedicated terminal window
echo [*] Launching FastAPI Backend Server (http://127.0.0.1:8000)...
start "Datazoic Backend API (FastAPI)" cmd /k "cd /d %~dp0 && call .venv\Scripts\activate.bat && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

:: 6. Wait 15 seconds for backend initialization and database seeding
echo [*] Waiting 15 seconds for backend initialization and database seeding...
timeout /t 15 /nobreak >nul 2>nul
echo [*] Backend initialization period complete!

:: 7. Launch Streamlit frontend in a separate dedicated terminal window
echo [*] Launching Streamlit Frontend (http://localhost:8501)...
start "Datazoic Frontend (Streamlit)" cmd /k "cd /d %~dp0 && call .venv\Scripts\activate.bat && python -m streamlit run frontend\app.py --server.port 8501 --server.headless true --browser.gatherUsageStats false"

:: 8. Wait 3 seconds then open browser
echo [*] Opening application in your web browser...
timeout /t 3 /nobreak >nul 2>nul
start http://localhost:8501

echo.
echo ============================================================================
echo   Datazoic Agentic System is running!
echo   - Keep both opened terminal windows running in the background.
echo   - Backend API:  http://127.0.0.1:8000
echo   - Frontend UI:  http://localhost:8501
echo   - Log in with:  admin@examplemail.com / admin@123
echo ============================================================================
echo.
pause
