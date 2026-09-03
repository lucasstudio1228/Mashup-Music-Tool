@echo off
REM ============================================================
REM  Mashup Music Tool - INSTALLER (chay 1 lan duy nhat)
REM  Tao virtual env + cai dependencies + torch CUDA.
REM  Yeu cau: Python 3.10-3.11 da cai (tick "Add to PATH").
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   MASHUP MUSIC TOOL - Dang cai dat...
echo ============================================================
echo.

REM --- Kiem tra Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Khong tim thay Python.
    echo Hay cai Python 3.10 hoac 3.11 tu https://www.python.org/downloads/
    echo Nho tick "Add Python to PATH" khi cai.
    pause
    exit /b 1
)

echo [1/4] Tao virtual environment (.venv)...
if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 (
        echo [LOI] Khong tao duoc virtual env.
        pause
        exit /b 1
    )
) else (
    echo       .venv da ton tai - bo qua.
)

echo [2/4] Nang cap pip...
call .venv\Scripts\python.exe -m pip install --upgrade pip

echo [3/4] Cai PyTorch (CUDA 12.1 - can GPU NVIDIA)...
echo       (Neu may KHONG co GPU NVIDIA, xoa " --index-url ..." de dung ban CPU - se cham hon)
call .venv\Scripts\pip.exe install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121
if errorlevel 1 (
    echo [CANH BAO] Cai torch CUDA that bai. Thu ban CPU...
    call .venv\Scripts\pip.exe install torch==2.5.1
)

echo [4/4] Cai cac thu vien con lai (co the mat vai phut)...
call .venv\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 (
    echo [LOI] Cai dependencies that bai.
    pause
    exit /b 1
)

REM --- Tao file .env neu chua co ---
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo.
        echo [!] Da tao file .env tu mau. Mo .env va dien ANTHROPIC_API_KEY neu can dung AI.
    )
)

echo.
echo ============================================================
echo   CAI DAT XONG! Chay start.bat de mo ung dung.
echo ============================================================
echo.
pause
endlocal
