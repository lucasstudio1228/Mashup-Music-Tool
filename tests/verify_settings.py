"""verify_settings.py – Test global settings endpoint.

CHẠY TRÊN DB TẠM (TestClient + override get_session), KHÔNG gọi backend :8000.
Bản cũ bắn PATCH thẳng vào localhost:8000 → ghi key giả, xoá key rồi "dọn dẹp"
về api.openai.com/gpt-4o-mini TRÊN DB THẬT của người dùng: ngày 2026-10-15 chạy
cả bộ test đã xoá mất key + base ShopAIKey → lô sản xuất lỗi 401 "Incorrect API
key provided". Test cấu hình KHÔNG BAO GIỜ được đụng data/app.db.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    import console  # noqa: F401 - UTF-8 output trên Windows
except ImportError:
    pass

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from backend import models  # noqa: E402,F401 - đăng ký bảng vào metadata
from backend.database import DB_PATH, get_session  # noqa: E402
from backend.routers import settings as settings_router  # noqa: E402

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"


def _real_settings_fingerprint():
    """(key, base, model) của DB thật — để chứng minh test không chạm vào nó."""
    import sqlite3
    if not DB_PATH.exists():
        return None
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        return con.execute("SELECT api_key, api_base_url, api_model "
                           "FROM appsettings WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def _client(db_file: Path):
    engine = create_engine(f"sqlite:///{db_file}",
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def _temp_session():
        with Session(engine) as s:
            yield s

    app = FastAPI()
    app.include_router(settings_router.router)
    app.dependency_overrides[get_session] = _temp_session
    return TestClient(app), engine


def run():
    results = []
    before = _real_settings_fingerprint()

    with tempfile.TemporaryDirectory() as tmp:
        c, engine = _client(Path(tmp) / "settings_test.db")

        # Test 1: GET settings trả về defaults khi chưa set
        r = c.get("/api/settings")
        results.append(("GET settings 200", r.status_code == 200))
        data = r.json()
        results.append(("has_api_key = False ban đầu",
                        data.get("has_api_key") == False))
        results.append(("api_base_url là string",
                        isinstance(data.get("api_base_url"), str)))
        results.append(("api_model là string", isinstance(data.get("api_model"), str)))
        results.append(("Không có field 'api_key' trong response",
                        "api_key" not in data))   # Security check

        # Test 2: PATCH set key
        r = c.patch("/api/settings", json={"api_key": "sk-test-12345"})
        results.append(("PATCH settings 200", r.status_code == 200))
        results.append(("has_api_key = True sau khi set",
                        r.json().get("has_api_key") == True))

        # Test 3: Key không lộ ra GET
        r = c.get("/api/settings")
        results.append(("Key không lộ sau khi set",
                        "sk-test-12345" not in str(r.json())))

        # Test 4: PATCH update base_url
        r = c.patch("/api/settings", json={"api_base_url": "https://custom.api.com"})
        results.append(("PATCH base_url", r.status_code == 200))
        results.append(("base_url cập nhật đúng",
                        r.json().get("api_base_url") == "https://custom.api.com"))

        # Test 5: Clear key
        r = c.patch("/api/settings", json={"clear_api_key": True})
        results.append(("Clear key 200", r.status_code == 200))
        results.append(("has_api_key = False sau khi clear",
                        r.json().get("has_api_key") == False))

        # Test 6: Empty string không overwrite key
        c.patch("/api/settings", json={"api_key": "sk-real-key"})
        c.patch("/api/settings", json={"api_key": "   "})
        r2 = c.get("/api/settings")
        results.append(("Empty string không xóa key",
                        r2.json().get("has_api_key") == True))

        # Test 7: đổi key KHÔNG được kéo base URL về mặc định (người dùng proxy
        # như ShopAIKey nhập lại key thì base URL đã lưu phải giữ nguyên).
        c.patch("/api/settings", json={"api_base_url": "https://api.shopaikey.com/v1"})
        r = c.patch("/api/settings", json={"api_key": "sk-another-key"})
        results.append(("Đổi key giữ nguyên base URL đã lưu",
                        r.json().get("api_base_url") == "https://api.shopaikey.com/v1"))
        c.close()
        engine.dispose()   # Windows: nhả khoá file SQLite trước khi xoá thư mục tạm

    results.append(("KHÔNG đụng cấu hình API trong data/app.db thật",
                    _real_settings_fingerprint() == before))

    print("\n=== SETTINGS VERIFY ===")
    all_pass = True
    for name, passed in results:
        print(f"{PASS if passed else FAIL} {name}")
        if not passed:
            all_pass = False
    print(f"\n{'✅ Settings PASSED' if all_pass else '❌ FAILED'}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    run()
