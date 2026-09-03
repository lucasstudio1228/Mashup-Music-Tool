@echo off
REM ============================================================
REM  Mashup Music Tool - START (double-click de chay)
REM  Chay server tren cong 8000 va tu mo trinh duyet.
REM ============================================================
setlocal
cd /d "%~dp0"

REM UTF-8 de tranh loi ky tu tieng Viet / emoji khi in log
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Chua cai dat. Hay chay install.bat truoc.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   MASHUP MUSIC TOOL dang khoi dong...
echo   Trinh duyet se tu mo tai: http://localhost:8000
echo   (De DONG ung dung: dong cua so nay hoac nhan Ctrl+C)
echo ============================================================
echo.

REM Mo trinh duyet sau 4 giay (cho server kip khoi dong)
start "" cmd /c "timeout /t 4 >nul & start "" http://localhost:8000"

REM Chay server (foreground) - dong cua so nay se tat server
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

endlocal
