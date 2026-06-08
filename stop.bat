@echo off
echo ========================================
echo    EMA Screener - Stop & Cleanup
echo ========================================
echo.

echo [1/3] Killing Python processes...
taskkill /F /IM python.exe >nul 2>&1
echo    Done

echo.
echo [2/3] Killing Streamlit processes...
taskkill /F /IM streamlit.exe >nul 2>&1
echo    Done

echo.
echo [3/3] Clearing Streamlit cache...
streamlit cache clear >nul 2>&1
echo    Done

echo.
echo ========================================
echo    Cleanup complete!
echo    Close ALL localhost browser tabs,
echo    then run start.bat to restart.
echo ========================================
pause
