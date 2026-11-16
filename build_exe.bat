@echo off
REM ============================================================
REM  Build MashupMusicTool.exe (ung dung desktop, khong can trinh duyet)
REM  Ket qua: MashupMusicTool.exe o thu muc nay (canh .venv, start.bat)
REM ============================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Chua cai dat. Hay chay install.bat truoc.
    pause
    exit /b 1
)

echo Cai pywebview + pyinstaller (neu chua co)...
.venv\Scripts\python.exe -m pip install -q pywebview pyinstaller || goto :fail

echo Dong goi launcher.py ...
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name MashupMusicTool --icon "%~dp0app.ico" ^
    --distpath data\_build\dist --workpath data\_build\work --specpath data\_build ^
    launcher.py || goto :fail

copy /Y "data\_build\dist\MashupMusicTool.exe" "MashupMusicTool.exe" >nul || (
    echo [LOI] Khong ghi de duoc MashupMusicTool.exe - hay dong ung dung dang mo roi build lai.
    goto :fail
)
echo.
echo XONG: %~dp0MashupMusicTool.exe
endlocal
exit /b 0

:fail
echo [LOI] Build that bai.
pause
exit /b 1
