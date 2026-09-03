"""run.py – Khởi động backend (FastAPI) + frontend (Vite) cùng lúc."""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true",
                        help="Enable hot-reload (do NOT use when a mix is rendering)")
    args = parser.parse_args()

    print("🎵 Starting MeditationMixer...")

    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "outputs").mkdir(exist_ok=True)
    (ROOT / "stems").mkdir(exist_ok=True)

    is_win = sys.platform == "win32"

    backend_cmd = [
        sys.executable, "-m", "uvicorn", "backend.main:app",
        "--host", "0.0.0.0", "--port", "8000",
    ]
    if args.dev:
        backend_cmd.append("--reload")
        print("  ⚠️  Dev mode: --reload enabled. Do not render mixes in this mode.")
    frontend_cmd = ["npm", "run", "dev"]

    procs = []
    try:
        procs.append(subprocess.Popen(backend_cmd, cwd=str(ROOT)))
        time.sleep(2)   # đợi backend khởi động
        procs.append(subprocess.Popen(
            frontend_cmd, cwd=str(ROOT / "frontend"), shell=is_win))

        print("\n✅ MeditationMixer running:")
        print("   Backend:  http://localhost:8000")
        print("   Frontend: http://localhost:5173")
        print("   API Docs: http://localhost:8000/docs")
        print("\nPress Ctrl+C to stop.\n")

        for proc in procs:
            proc.wait()

    except KeyboardInterrupt:
        print("\nShutting down...")
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("Stopped.")


if __name__ == "__main__":
    main()
