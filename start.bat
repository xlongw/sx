@echo off
echo ========================================
echo    EMA Screener - Safe Start
echo ========================================
echo.

echo [1/5] Killing old processes...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM streamlit.exe >nul 2>&1
echo    Done

echo.
echo [2/5] Clearing Streamlit cache...
streamlit cache clear >nul 2>&1
echo    Done

echo.
echo [3/5] Waiting for port release...
timeout /t 2 /nobreak >nul
echo    Done

echo.
echo [4/5] Checking port...
set PORT=8501
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul
if %errorlevel% equ 0 (
    echo    Port 8501 in use, trying 8502...
    set PORT=8502
)
echo    Using port: %PORT%

echo.
echo [5/5] Starting app + opening browser...
set T=%time::=%
set T=%T: =0%
set TS=%date:~0,4%%date:~5,2%%date:~8,2%%T:~0,6%
set URL=http://localhost:%PORT%/?v=%TS%
echo    URL: %URL%
start "" "%URL%"
echo.
echo    Press Ctrl+C to stop
echo ========================================
streamlit run app.py --server.port %PORT%
