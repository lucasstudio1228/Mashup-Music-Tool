"""
video/job_manager.py — Queue 1 worker cho tác vụ Video (Playwright rất nặng,
không chạy song song). SSE progress theo project_id. Mẫu giống stem_job_manager.
"""
from __future__ import annotations
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VideoJob:
    project_id: int
    kind: str                      # images | clips | assemble | full
    status: str = "pending"        # pending|running|completed|failed
    percent: float = 0.0
    message: str = ""
    error: Optional[str] = None
    result: Optional[dict] = None


class VideoJobManager:
    def __init__(self):
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="video_worker")
        self._jobs: dict[int, VideoJob] = {}
        self._queues: dict[int, asyncio.Queue] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def bind_loop(self, loop): self._loop = loop

    def is_busy(self) -> bool:
        with self._lock:
            return any(j.status in ("pending", "running")
                       for j in self._jobs.values())

    def get(self, project_id: int) -> Optional[VideoJob]:
        with self._lock:
            return self._jobs.get(project_id)

    def submit(self, project_id: int, kind: str, fn, *args, **kwargs) -> None:
        with self._lock:
            self._jobs[project_id] = VideoJob(project_id=project_id, kind=kind)
            self._queues.setdefault(project_id, asyncio.Queue())
        self._executor.submit(self._run, project_id, fn, args, kwargs)

    def _run(self, project_id, fn, args, kwargs):
        self._update(project_id, status="running")
        try:
            result = fn(project_id, self._progress(project_id), *args, **kwargs)
            self._update(project_id, status="completed", percent=100.0,
                         result=result if isinstance(result, dict) else None)
            self._push(project_id, {"type": "done", "status": "completed",
                                    "result": result if isinstance(result, dict) else None})
        except Exception as exc:                          # noqa: BLE001
            import traceback
            msg = str(exc).strip()
            detail = f"{type(exc).__name__}: {msg}" if msg else \
                     f"{type(exc).__name__} (không có message)"
            detail += "\n" + traceback.format_exc()[-800:]
            self._update(project_id, status="failed", error=detail)
            self._push(project_id, {"type": "error", "message": detail})
            self._push(project_id, {"type": "done", "status": "failed"})

    def _progress(self, project_id):
        def cb(message: str, percent: float):
            self._update(project_id, message=message, percent=percent)
            self._push(project_id, {"type": "progress",
                                    "message": message,
                                    "percent": round(percent, 1)})
        return cb

    def _update(self, project_id, **kw):
        with self._lock:
            job = self._jobs.get(project_id)
            if job:
                for k, v in kw.items():
                    setattr(job, k, v)

    def _queue(self, project_id) -> asyncio.Queue:
        with self._lock:
            return self._queues.setdefault(project_id, asyncio.Queue())

    def _push(self, project_id, event: dict):
        q = self._queue(project_id)
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(q.put(event), loop)

    async def stream(self, project_id: int):
        with self._lock:
            job = self._jobs.get(project_id)
        if job and job.status in ("completed", "failed"):
            if job.status == "completed":
                yield {"type": "progress", "message": job.message,
                       "percent": 100.0}
                yield {"type": "done", "status": "completed",
                       "result": job.result}
            else:
                yield {"type": "error", "message": job.error or "Unknown"}
                yield {"type": "done", "status": "failed"}
            return
        q = self._queue(project_id)
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield event
                if event.get("type") == "done":
                    break
            except asyncio.TimeoutError:
                yield {"type": "ping"}


video_job_manager = VideoJobManager()
