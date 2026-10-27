"""Chốt chặn cho test bắn vào backend THẬT (localhost:8000).

Các test này tạo/xoá project, quét track, render mix… trên DB + thư mục media
THẬT của người dùng. Ngày 2026-10-15 chạy cả bộ tests/verify_*.py đã để lại
project rác (#50 «Mix Test», #52 «StemTest») và xoá mất cấu hình API thật.
Vì vậy mặc định BỎ QUA; chỉ chạy khi người dùng chủ động bật:

    set MM_LIVE_BACKEND_TESTS=1   (cmd)   |   export MM_LIVE_BACKEND_TESTS=1
"""
import os
import sys


def require_live_opt_in(name: str) -> None:
    if os.environ.get("MM_LIVE_BACKEND_TESTS") == "1":
        return
    print(f"⏭️  SKIP {name}: test này ghi vào backend THẬT :8000 (DB + media của "
          "bạn). Đặt MM_LIVE_BACKEND_TESTS=1 nếu thực sự muốn chạy.")
    sys.exit(0)
