import os
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .jobs import RenderJobQueue
from .paths import DEFAULT_JOBS_ROOT

app = FastAPI(title="HDT Render", version="0.1.0")


def jobs_root() -> Path:
    root = Path(os.environ.get("HDT_RENDER_JOBS_ROOT", DEFAULT_JOBS_ROOT)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


render_jobs = RenderJobQueue(jobs_root())


@app.get("/v1/render/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/v1/render/jobs")
def list_jobs(client_request_id: str | None = Query(default=None)) -> dict:
    return {"ok": True, "jobs": render_jobs.list_jobs(client_request_id)}


@app.post("/v1/render/jobs")
async def create_render_job(
    headline: str = Form(...),
    script: str = Form(...),
    narration: UploadFile = File(...),
    width: int = Form(1920),
    height: int = Form(1080),
    fps: int = Form(30),
    use_nvenc: bool = Form(True),
    client_request_id: str | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> JSONResponse:
    validate_render_request(headline, script, narration, width, height, fps)
    job, _created = await run_in_threadpool(
        render_jobs.enqueue,
        headline=headline,
        script=script,
        narration=narration.file,
        width=width,
        height=height,
        fps=fps,
        use_nvenc=use_nvenc,
        client_request_id=client_request_id or idempotency_key,
    )
    return JSONResponse({"ok": True, "job": job}, status_code=202)


@app.get("/v1/render/jobs/{job_id}/download")
def download_render_job(job_id: str) -> FileResponse:
    job = render_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="render job not found")
    output_path = render_jobs.output_path(job_id)
    if not output_path:
        raise HTTPException(status_code=409, detail="render job output is not ready")
    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"{job_id}.mp4",
        headers={"X-HDT-Render-Job": job_id},
    )


@app.get("/v1/render/jobs/{job_id}")
def get_render_job(job_id: str) -> dict:
    job = render_jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="render job not found")
    return {"ok": True, "job": job}


@app.post("/v1/render")
async def render_endpoint(
    headline: str = Form(...),
    script: str = Form(...),
    narration: UploadFile = File(...),
    width: int = Form(1920),
    height: int = Form(1080),
    fps: int = Form(30),
    use_nvenc: bool = Form(True),
    client_request_id: str | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> FileResponse:
    validate_render_request(headline, script, narration, width, height, fps)
    job, _created = await run_in_threadpool(
        render_jobs.enqueue,
        headline=headline,
        script=script,
        narration=narration.file,
        width=width,
        height=height,
        fps=fps,
        use_nvenc=use_nvenc,
        client_request_id=client_request_id or idempotency_key,
    )
    completed = await run_in_threadpool(render_jobs.wait_for_completion, job["id"])
    if completed["status"] != "completed":
        raise HTTPException(status_code=500, detail=completed.get("error") or "render job failed")
    output_path = render_jobs.output_path(job["id"])
    if not output_path:
        raise HTTPException(status_code=500, detail="render job completed but output.mp4 is missing")
    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"{job['id']}.mp4",
        headers={"X-HDT-Render-Job": job["id"]},
    )


def validate_render_request(headline: str, script: str, narration: UploadFile, width: int, height: int, fps: int) -> None:
    if not headline.strip():
        raise HTTPException(status_code=400, detail="headline is required")
    if not script.strip():
        raise HTTPException(status_code=400, detail="script is required")
    if not narration.filename:
        raise HTTPException(status_code=400, detail="narration file is required")
    if width <= 0 or height <= 0:
        raise HTTPException(status_code=400, detail="width and height must be positive")
    if fps <= 0:
        raise HTTPException(status_code=400, detail="fps must be positive")


def main() -> None:
    import uvicorn

    host = os.environ.get("HDT_RENDER_HOST", "0.0.0.0")
    port = int(os.environ.get("HDT_RENDER_PORT", "8088"))
    uvicorn.run("hdt_render.server:app", host=host, port=port)
