"""verify_settings.py – Test global settings endpoint."""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    import console  # noqa: F401 - UTF-8 output trên Windows
except ImportError:
    pass

BASE = "http://localhost:8000"
PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"


def run():
    results = []

    # Test 1: GET settings trả về defaults khi chưa set
    r = requests.get(f"{BASE}/api/settings")
    results.append(("GET settings 200", r.status_code == 200))
    data = r.json()
    results.append(("has_api_key = False ban đầu",
                    data.get("has_api_key") == False))
    results.append(("api_base_url là string", isinstance(data.get("api_base_url"), str)))
    results.append(("api_model là string", isinstance(data.get("api_model"), str)))
    results.append(("Không có field 'api_key' trong response",
                    "api_key" not in data))   # Security check

    # Test 2: PATCH set key
    r = requests.patch(f"{BASE}/api/settings",
                       json={"api_key": "sk-test-12345"})
    results.append(("PATCH settings 200", r.status_code == 200))
    results.append(("has_api_key = True sau khi set",
                    r.json().get("has_api_key") == True))

    # Test 3: Key không lộ ra GET
    r = requests.get(f"{BASE}/api/settings")
    results.append(("Key không lộ sau khi set",
                    "sk-test-12345" not in str(r.json())))

    # Test 4: PATCH update base_url
    r = requests.patch(f"{BASE}/api/settings",
                       json={"api_base_url": "https://custom.api.com"})
    results.append(("PATCH base_url", r.status_code == 200))
    results.append(("base_url cập nhật đúng",
                    r.json().get("api_base_url") == "https://custom.api.com"))

    # Test 5: Clear key
    r = requests.patch(f"{BASE}/api/settings",
                       json={"clear_api_key": True})
    results.append(("Clear key 200", r.status_code == 200))
    results.append(("has_api_key = False sau khi clear",
                    r.json().get("has_api_key") == False))

    # Test 6: Empty string không overwrite key
    requests.patch(f"{BASE}/api/settings", json={"api_key": "sk-real-key"})
    r = requests.patch(f"{BASE}/api/settings", json={"api_key": "   "})
    r2 = requests.get(f"{BASE}/api/settings")
    results.append(("Empty string không xóa key",
                    r2.json().get("has_api_key") == True))

    # Reset về clean state
    requests.patch(f"{BASE}/api/settings", json={"clear_api_key": True,
                   "api_base_url": "https://api.anthropic.com",
                   "api_model": "claude-haiku-4-5-20251001"})

    print("\n=== SETTINGS VERIFY ===")
    all_pass = True
    for name, passed in results:
        print(f"{PASS if passed else FAIL} {name}")
        if not passed:
            all_pass = False
    print(f"\n{'✅ Settings PASSED' if all_pass else '❌ FAILED'}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    try:
        requests.get(f"{BASE}/api/health", timeout=3)
    except Exception:
        print("❌ Backend chưa chạy. Khởi động: python run.py")
        sys.exit(2)
    run()
