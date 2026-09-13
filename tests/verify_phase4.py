"""verify_phase4.py – Test track_namer với và không có API."""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from api_config import APIConfig
from track_namer import generate_names

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"

def run():
    results = []

    # Test 1: Fallback (không có API key)
    cfg_no_key = APIConfig()
    cfg_no_key.api_key = None
    names = generate_names(20, cfg_no_key)
    results.append(("Fallback trả đúng số lượng", len(names) == 20))
    results.append(("Fallback không có duplicate", len(set(names)) == 20))
    results.append(("Fallback trả list[str]", all(isinstance(n, str) for n in names)))

    # Test 2: Fallback với số lượng lớn
    names_big = generate_names(50, cfg_no_key)
    results.append(("Fallback hoạt động với 50 names", len(names_big) == 50))

    # Test 3: API nếu có key trong env
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        cfg = APIConfig()
        try:
            names_api = generate_names(5, cfg)
            results.append(("API trả đúng 5 names", len(names_api) == 5))
            results.append(("API names unique", len(set(names_api)) == 5))
            results.append(("API names là strings", all(isinstance(n, str) for n in names_api)))
        except Exception as e:
            results.append((f"API call thất bại: {e}", False))
    else:
        print("  ℹ️  OPENAI_API_KEY không set – bỏ qua API live test")

    print("\n=== PHASE 4 VERIFICATION ===")
    all_pass = True
    for name, passed in results:
        print(f"{PASS if passed else FAIL} {name}")
        if not passed:
            all_pass = False
    print(f"\n{'✅ Phase 4 PASSED' if all_pass else '❌ Phase 4 FAILED – fix trước khi tiếp tục'}")
    sys.exit(0 if all_pass else 1)

if __name__ == "__main__":
    run()
