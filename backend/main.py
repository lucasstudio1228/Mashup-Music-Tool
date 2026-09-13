"""FastAPI app: CORS, startup, router wiring."""
import asyncio
import os
import sys

# Fix Windows event loop TRƯỚC KHI import uvicorn / bất kỳ thứ gì dùng loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI                       # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from backend.database import create_db_and_tables  # noqa: E402
from backend.job_manager import job_manager        # noqa: E402
from backend.migrations import run_all as run_migrations  # noqa: E402
from backend.routers import mixes, projects, settings, tracks  # noqa: E402
from backend.routers.stems import router as stems_router        # noqa: E402
from backend.routers.video import router as video_router        # noqa: E402
from backend.stem_job_manager import stem_job_manager           # noqa: E402
from backend.video.job_manager import video_job_manager         # noqa: E402

app = FastAPI(title="MeditationMixer API")

_raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173")
CORS_ORIGINS = [o.strip() for o in _raw_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    run_migrations()          # TRƯỚC create_db_and_tables
    create_db_and_tables()
    # Worker thread cần loop này để push SSE event.
    loop = asyncio.get_running_loop()
    job_manager.bind_loop(loop)
    stem_job_manager.bind_loop(loop)
    video_job_manager.bind_loop(loop)   # ← phần Video


app.include_router(projects.router, prefix="/api")
app.include_router(tracks.router, prefix="/api")
app.include_router(mixes.router, prefix="/api")
app.include_router(settings.router)
app.include_router(stems_router)   # đã có prefix="/api" nội bộ
app.include_router(video_router)   # đã có prefix nội bộ


@app.get("/api/health")
def health():
    return {"status": "ok", "active_jobs": job_manager.active_count()}


# ─── Serve built frontend (production / distributable) ──────────────
from pathlib import Path                              # noqa: E402
from fastapi.staticfiles import StaticFiles           # noqa: E402
from fastapi.responses import FileResponse            # noqa: E402

_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if (_FRONTEND_DIST / "index.html").exists():
    _assets_dir = _FRONTEND_DIST / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    @app.get("/")
    def _serve_index():
        return FileResponse(str(_FRONTEND_DIST / "index.html"))

    # SPA catch-all: file tĩnh thì trả file, còn lại trả index.html
    @app.get("/{full_path:path}")
    def _serve_spa(full_path: str):
        candidate = _FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_FRONTEND_DIST / "index.html"))
