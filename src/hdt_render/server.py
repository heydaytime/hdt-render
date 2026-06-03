import os
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from .paths import DEFAULT_JOBS_ROOT
from .renderer import render_mp4

app = FastAPI(title="HDT Render", version="0.1.0")


def jobs_root() -> Path:
    root = Path(os.environ.get("HDT_RENDER_JOBS_ROOT", DEFAULT_JOBS_ROOT)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/v1/render")
async def render_endpoint(
    headline: str = Form(...),
    script: str = Form(...),
    narration: UploadFile = File(...),
    width: int = Form(1920),
    height: int = Form(1080),
    fps: int = Form(30),
    use_nvenc: bool = Form(True),
) -> FileResponse:
    if not headline.strip():
        raise HTTPException(status_code=400, detail="headline is required")
    if not script.strip():
        raise HTTPException(status_code=400, detail="script is required")
    if not narration.filename:
        raise HTTPException(status_code=400, detail="narration file is required")

    job_id = f"render_{uuid.uuid4().hex}"
    job_dir = jobs_root() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    narration_path = job_dir / "narration.wav"
    output_path = job_dir / "output.mp4"
    script_path = job_dir / "script.txt"
    script_path.write_text(script, encoding="utf-8")

    with narration_path.open("wb") as handle:
        shutil.copyfileobj(narration.file, handle)

    try:
        await run_in_threadpool(
            render_mp4,
            headline=headline,
            narration_path=narration_path,
            output_path=output_path,
            width=width,
            height=height,
            fps=fps,
            use_nvenc=use_nvenc,
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"{job_id}.mp4",
        headers={"X-HDT-Render-Job": job_id},
    )


def main() -> None:
    import uvicorn

    host = os.environ.get("HDT_RENDER_HOST", "0.0.0.0")
    port = int(os.environ.get("HDT_RENDER_PORT", "8088"))
    uvicorn.run("hdt_render.server:app", host=host, port=port)
