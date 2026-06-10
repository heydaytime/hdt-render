# HDT Render

Remote MP4 render service for HDT News.

This repo turns a news headline plus a narration WAV into a finished MP4 with the HDT studio layout, scrolling lower-third, and animated Live2D host. It is designed to run on the RTX render server behind the HeyDayTime public network.

## What It Does

- Accepts a script string, headline, and uploaded WAV narration file.
- Renders the ChoDenPA Live2D model with narration-driven mouth movement.
- Composites the host, studio background, headline graphics, lower-third banner, ticker, logo placeholder, and audio.
- Returns a finished `video/mp4`.

The service does **not** generate TTS itself in v1. The caller sends the narration WAV. This keeps the renderer API simple: send script plus audio in, get MP4 out.

## API

### Health

```bash
curl http://localhost:8088/v1/render/health
```

### Render MP4

```bash
curl -X POST http://localhost:8088/v1/render \
  -F 'headline=Iran Talks Stall as Ebola Outbreak Escalates' \
  -F 'script=The full narration script used to produce the uploaded WAV.' \
  -F 'narration=@tmp/test-narration.wav;type=audio/wav' \
  -o output.mp4
```

The response body is the MP4 file. The response includes an `X-HDT-Render-Job` header with the internal job id.

### Async Render Job

```bash
curl -X POST http://localhost:8088/v1/render/jobs \
  -H 'Idempotency-Key: broadcast-chunk-123' \
  -F 'headline=Iran Talks Stall as Ebola Outbreak Escalates' \
  -F 'script=The full narration script used to produce the uploaded WAV.' \
  -F 'narration=@tmp/test-narration.wav;type=audio/wav'
```

Response:

```json
{
  "ok": true,
  "job": {
    "id": "render_1780214027473",
    "status": "queued",
    "stage": "Queued",
    "progress": 0,
    "clientRequestId": "broadcast-chunk-123",
    "headline": "Iran Talks Stall as Ebola Outbreak Escalates",
    "scriptHash": "sha256...",
    "createdAt": "2026-06-07T00:00:00.000Z",
    "updatedAt": "2026-06-07T00:00:00.000Z",
    "downloadUrl": "/v1/render/jobs/render_1780214027473/download"
  }
}
```

Poll the job:

```bash
curl http://localhost:8088/v1/render/jobs/render_1780214027473
```

Recover by idempotency key if the original connection fails before you receive the job id:

```bash
curl 'http://localhost:8088/v1/render/jobs?client_request_id=broadcast-chunk-123'
```

Download the completed MP4:

```bash
curl http://localhost:8088/v1/render/jobs/render_1780214027473/download -o output.mp4
```

Optional form fields:

- `width`, default `1920`
- `height`, default `1080`
- `fps`, default `30`
- `use_nvenc`, default `true`
- `client_request_id`, optional stable idempotency key. The `Idempotency-Key` header is also accepted.

## Runtime Requirements

- Python 3.12+
- FFmpeg
- NVIDIA driver for the RTX server
- Working X/OpenGL environment for `live2d-py`
- CMake/OpenGL development headers to compile `live2d-py`

On Pop!_OS/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y \
  ffmpeg xvfb mesa-utils python3-venv python3-pip \
  cmake build-essential ninja-build pkg-config \
  libgl1 libgl-dev libglx-dev libopengl-dev libglu1-mesa libglu1-mesa-dev \
  libx11-6 libx11-dev libxrandr2 libxinerama1 libxcursor1 libxi6 libxext6
```

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

## Run

```bash
. .venv/bin/activate
HDT_RENDER_PORT=8088 hdt-render-api
```

For SSH/headless testing, use Xvfb:

```bash
HDT_RENDER_PORT=8088 xvfb-run -a .venv/bin/hdt-render-api
```

Port `8088` is intentional for the current server because port `8000` is already used by the TTS service.

## Systemd

The RTX server runs the renderer through systemd:

```bash
sudo systemctl status hdt-render.service
sudo systemctl restart hdt-render.service
sudo journalctl -u hdt-render.service -n 80 --no-pager
```

Installed unit:

```txt
deploy/hdt-render.service -> /etc/systemd/system/hdt-render.service
```

The service runs as `heyday`, starts on boot, uses `/home/heyday/hdt-render-test` as its working directory, binds `0.0.0.0:8088`, and launches through `xvfb-run` for the Live2D OpenGL context.

## CLI Smoke Test

```bash
. .venv/bin/activate
python scripts/make_test_wav.py
xvfb-run -a hdt-render-cli \
  --headline "HDT Render Smoke Test" \
  --narration tmp/test-narration.wav \
  --output tmp/smoke.mp4
ffprobe -v error -show_entries format=duration,size -of json tmp/smoke.mp4
```

## Production Shape

The HDT backend should:

1. Generate or obtain the final narration WAV.
2. Submit `headline`, `script`, and the WAV file to `/v1/render/jobs` with a stable `client_request_id`.
3. Poll `/v1/render/jobs/:id`.
4. Download the MP4 from `/v1/render/jobs/:id/download` when completed.

The render service stores per-request intermediates under `jobs/` by default. Override with:

```bash
HDT_RENDER_JOBS_ROOT=/srv/hdt-render/jobs
```

Request contract:

- Input: multipart form with `headline`, `script`, and a 16-bit PCM WAV `narration` upload.
- Sync output: `POST /v1/render` response body is the rendered MP4.
- Async output: `POST /v1/render/jobs` response body is a JSON job snapshot with a durable job id.
- Header: sync render responses include `X-HDT-Render-Job` with the internal job directory name.
- Current behavior: `script` is validated and saved for traceability; the visual render is driven by `headline` plus the uploaded narration WAV.

Async job statuses:

- `queued`
- `rendering`
- `completed`
- `failed`
- `cancelled`

Only one render runs at a time. On startup, queued jobs are resumed. Jobs that were rendering when the service stopped are retried once, then marked failed if they are found rendering again after another restart.

## Encoding

The service tries NVIDIA hardware encode first:

```txt
h264_nvenc
```

If NVENC fails, it falls back to CPU `libx264`. The Live2D host layer is still rendered through OpenGL and encoded as a temporary alpha-preserving FFV1/BGRA MKV before final compositing.

When running over plain SSH with `xvfb-run`, OpenGL may use Mesa llvmpipe for the Live2D drawing stage. That is acceptable for a headless smoke test. The final MP4 encode can still use NVIDIA NVENC as long as FFmpeg exposes `h264_nvenc`.

Check NVENC availability with:

```bash
ffmpeg -hide_banner -encoders | grep h264_nvenc
```

## Tested On

Validated on the `ssh server` RTX machine:

- Pop!_OS/Ubuntu userland
- Python 3.12.3
- NVIDIA GeForce RTX 5060 Ti
- FFmpeg with `h264_nvenc`
- `live2d-py==0.7.0`

Smoke test command used:

```bash
curl -X POST http://127.0.0.1:8088/v1/render \
  -F 'headline=HDT Render API Smoke Test' \
  -F 'script=This is a short render API smoke test.' \
  -F 'narration=@tmp/test-narration.wav;type=audio/wav' \
  -o tmp/api-output.mp4
```

Validated output:

- Video: H.264, `1920x1080`, `30 fps`
- Audio: AAC, mono, `24000 Hz`
- Duration: `4.000000` seconds for the included smoke WAV

## Assets

Bundled assets:

- `assets/backgrounds/studio.png`
- `assets/live2d/chodenpa_student/VT_student.model3.json`
- Live2D textures, physics, expressions, and fonts

## Current Layout

- Output: `1920x1080`, 30 fps
- Host layer: `650x840`
- Host crop: waist-up, lowered to avoid top hair clipping
- Lower-third masks the body below the waistline
