"""Mở thư mục trên máy (tool chạy local) — Audio (mix) và Final dùng chung.

Có file → Explorer mở thư mục và chọn sẵn file (`explorer /select,`);
chỉ có thư mục → mở thư mục. Chỉ Windows (501 nơi khác).
"""
import os
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import HTTPException


def reveal(folder: Path, select: Optional[Path] = None) -> dict:
    if os.name != "nt" or not hasattr(os, "startfile"):
        raise HTTPException(501, "Chỉ hỗ trợ mở thư mục trên Windows.")
    folder = Path(folder)
    if not folder.is_dir():
        raise HTTPException(404, f"Thư mục chưa tồn tại: {folder}")
    try:
        if select is not None and Path(select).is_file():
            # explorer luôn trả mã 1 → không check; không qua shell.
            subprocess.Popen(["explorer", f"/select,{Path(select).resolve()}"])
            return {"status": "opened", "folder": str(folder), "selected": str(select)}
        os.startfile(str(folder))
    except OSError as e:
        raise HTTPException(500, f"Không mở được thư mục: {e}")
    return {"status": "opened", "folder": str(folder), "selected": None}
