from __future__ import annotations

import hashlib
import json
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Literal

from .renderer import render_mp4

JobStatus = Literal["queued", "rendering", "completed", "failed", "cancelled"]

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "rendering"}
MAX_RENDER_ATTEMPTS = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def script_hash(script: str) -> str:
    return hashlib.sha256(script.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


class RenderJobQueue:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._jobs: dict[str, dict] = {}
        self._queue: list[str] = []
        self._active_id: str | None = None
        self._load_existing_jobs()
        self._worker = threading.Thread(target=self._worker_loop, name="hdt-render-worker", daemon=True)
        self._worker.start()

    def enqueue(
        self,
        *,
        headline: str,
        script: str,
        narration: BinaryIO,
        width: int,
        height: int,
        fps: int,
        use_nvenc: bool,
        client_request_id: str | None,
    ) -> tuple[dict, bool]:
        normalized_client_id = client_request_id.strip() if client_request_id else None
        with self._condition:
            if normalized_client_id:
                existing = self._find_active_by_client_request_id(normalized_client_id)
                if existing:
                    return self.public_job(existing), False

            job_id = f"render_{uuid.uuid4().hex}"
            job_dir = self.root / job_id
            job_dir.mkdir(parents=True, exist_ok=False)
            now = utc_now()
            job = {
                "id": job_id,
                "status": "queued",
                "stage": "Queued",
                "progress": 0,
                "clientRequestId": normalized_client_id,
                "headline": headline.strip(),
                "scriptHash": script_hash(script),
                "width": width,
                "height": height,
                "fps": fps,
                "useNvenc": use_nvenc,
                "attempts": 0,
                "error": None,
                "createdAt": now,
                "updatedAt": now,
                "startedAt": None,
                "completedAt": None,
            }
            (job_dir / "script.txt").write_text(script, encoding="utf-8")
            with (job_dir / "narration.wav").open("wb") as handle:
                shutil.copyfileobj(narration, handle)
            self._jobs[job_id] = job
            self._queue.append(job_id)
            self._persist(job)
            self._condition.notify_all()
            return self.public_job(job), True

    def list_jobs(self, client_request_id: str | None = None) -> list[dict]:
        normalized_client_id = client_request_id.strip() if client_request_id else None
        with self._lock:
            jobs = list(self._jobs.values())
            if normalized_client_id:
                jobs = [job for job in jobs if job.get("clientRequestId") == normalized_client_id]
            return [self.public_job(job) for job in sorted(jobs, key=lambda item: item.get("createdAt", ""), reverse=True)]

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self.public_job(job) if job else None

    def output_path(self, job_id: str) -> Path | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") != "completed":
                return None
            path = self._job_dir(job_id) / "output.mp4"
            return path if path.exists() else None

    def wait_for_completion(self, job_id: str) -> dict:
        with self._condition:
            while True:
                job = self._jobs.get(job_id)
                if not job:
                    raise KeyError(job_id)
                if job.get("status") in TERMINAL_STATUSES:
                    return self.public_job(job)
                self._condition.wait()

    def public_job(self, job: dict) -> dict:
        return {
            "id": job["id"],
            "status": job["status"],
            "stage": job["stage"],
            "progress": job["progress"],
            "clientRequestId": job.get("clientRequestId"),
            "headline": job["headline"],
            "scriptHash": job["scriptHash"],
            "width": job["width"],
            "height": job["height"],
            "fps": job["fps"],
            "useNvenc": job["useNvenc"],
            "attempts": job.get("attempts", 0),
            "error": job.get("error"),
            "createdAt": job["createdAt"],
            "updatedAt": job["updatedAt"],
            "startedAt": job.get("startedAt"),
            "completedAt": job.get("completedAt"),
            "downloadUrl": f"/v1/render/jobs/{job['id']}/download",
        }

    def _load_existing_jobs(self) -> None:
        for job_json in sorted(self.root.glob("render_*/job.json")):
            try:
                job = json.loads(job_json.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(job, dict) or not isinstance(job.get("id"), str):
                continue
            job_id = job["id"]
            status = job.get("status")
            if status == "completed" and not (self._job_dir(job_id) / "output.mp4").exists():
                self._mark_loaded_failed(job, "completed_job_missing_output")
            elif status == "rendering":
                attempts = int(job.get("attempts") or 0)
                if attempts < MAX_RENDER_ATTEMPTS:
                    self._mark_loaded_queued(job, "Queued after backend restart during render")
                else:
                    self._mark_loaded_failed(job, "server_restarted_during_render")
            elif status == "queued":
                self._queue.append(job_id)
            elif status not in TERMINAL_STATUSES:
                self._mark_loaded_failed(job, f"unknown_job_status: {status}")
            self._jobs[job_id] = job
            self._persist(job)

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._queue:
                    self._condition.wait()
                job_id = self._queue.pop(0)
                job = self._jobs.get(job_id)
                if not job or job.get("status") != "queued":
                    continue
                self._active_id = job_id
                now = utc_now()
                job["status"] = "rendering"
                job["stage"] = "Rendering MP4"
                job["progress"] = 50
                job["attempts"] = int(job.get("attempts") or 0) + 1
                job["startedAt"] = job.get("startedAt") or now
                job["updatedAt"] = now
                job["error"] = None
                self._persist(job)
                self._condition.notify_all()

            try:
                self._render(job)
                with self._condition:
                    now = utc_now()
                    job["status"] = "completed"
                    job["stage"] = "Completed"
                    job["progress"] = 100
                    job["updatedAt"] = now
                    job["completedAt"] = now
                    job["error"] = None
                    self._persist(job)
            except Exception as error:
                with self._condition:
                    now = utc_now()
                    job["status"] = "failed"
                    job["stage"] = "Failed"
                    job["progress"] = 0
                    job["updatedAt"] = now
                    job["completedAt"] = now
                    job["error"] = str(error)
                    self._persist(job)
            finally:
                with self._condition:
                    self._active_id = None
                    self._condition.notify_all()

    def _render(self, job: dict) -> None:
        job_dir = self._job_dir(job["id"])
        output_path = job_dir / "output.mp4"
        render_mp4(
            headline=job["headline"],
            narration_path=job_dir / "narration.wav",
            output_path=output_path,
            width=int(job["width"]),
            height=int(job["height"]),
            fps=int(job["fps"]),
            use_nvenc=bool(job["useNvenc"]),
        )
        if not output_path.exists():
            raise RuntimeError("render completed but output.mp4 was not created")

    def _find_active_by_client_request_id(self, client_request_id: str) -> dict | None:
        matches = [
            job for job in self._jobs.values()
            if job.get("clientRequestId") == client_request_id and job.get("status") not in {"failed", "cancelled"}
        ]
        return sorted(matches, key=lambda item: item.get("createdAt", ""), reverse=True)[0] if matches else None

    def _mark_loaded_queued(self, job: dict, stage: str) -> None:
        now = utc_now()
        job["status"] = "queued"
        job["stage"] = stage
        job["progress"] = 0
        job["updatedAt"] = now
        job["error"] = None
        self._queue.append(job["id"])

    def _mark_loaded_failed(self, job: dict, error: str) -> None:
        now = utc_now()
        job["status"] = "failed"
        job["stage"] = "Failed"
        job["progress"] = 0
        job["updatedAt"] = now
        job["completedAt"] = job.get("completedAt") or now
        job["error"] = error

    def _persist(self, job: dict) -> None:
        job_dir = self._job_dir(job["id"])
        job_dir.mkdir(parents=True, exist_ok=True)
        path = job_dir / "job.json"
        tmp_path = job_dir / "job.json.tmp"
        tmp_path.write_text(json.dumps(job, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)

    def _job_dir(self, job_id: str) -> Path:
        return self.root / job_id
