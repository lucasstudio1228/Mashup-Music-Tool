"""Launcher .exe nho — chi de double-click chay start.bat.

Build:  pyinstaller --onefile --name MashupMusicTool launcher.py
Ket qua: dist/MashupMusicTool.exe  (copy ra thu muc goc, canh start.bat)
"""
import os
import subprocess
import sys


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        # Khi da dong goi thanh .exe -> lay thu muc chua file .exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    base = _base_dir()
    bat = os.path.join(base, "start.bat")
    if not os.path.exists(bat):
        print("Khong tim thay start.bat canh file nay.")
        input("Nhan Enter de thoat...")
        return 1
    # Chay start.bat, ke thua console hien tai (dong cua so = tat server)
    return subprocess.run(["cmd", "/c", bat], cwd=base).returncode


if __name__ == "__main__":
    raise SystemExit(main())
