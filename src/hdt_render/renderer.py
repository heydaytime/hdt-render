import math
import os
import subprocess
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .paths import ASSET_ROOT, DEFAULT_BACKGROUND_PATH, DEFAULT_HOST_MODEL_PATH

FONT_ROOT = ASSET_ROOT / "fonts"
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")

BANNER_HEIGHT = 158
BANNER_MARGIN_X = 58
LOGO_WIDTH = 255
LOGO_GAP = 28
TICKER_SPEED = 124
HOST_WIDTH = 650
HOST_HEIGHT = 840
HOST_MODEL_SCALE = 3.35
HOST_MODEL_OFFSET_X = 0.0
HOST_MODEL_OFFSET_Y = -2.05
HOST_MARGIN_RIGHT = 112
HOST_BASELINE_OVERLAP = 42


def render_mp4(
    *,
    headline: str,
    narration_path: Path,
    output_path: Path,
    background_path: Path = DEFAULT_BACKGROUND_PATH,
    host_model_path: Path = DEFAULT_HOST_MODEL_PATH,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    use_nvenc: bool = True,
) -> None:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base_path = output_path.parent / "base.png"
    banner_path = output_path.parent / "banner.png"
    ticker_path = output_path.parent / "ticker-strip.png"
    logo_path = output_path.parent / "logo-placeholder.png"
    host_video_path = output_path.parent / "live2d-host.mkv"

    ticker_period = render_assets(
        base_path=base_path,
        banner_path=banner_path,
        ticker_path=ticker_path,
        logo_path=logo_path,
        background_path=background_path,
        headline=headline or "HDT News Update",
        width=width,
        height=height,
    )
    render_live2d_host_video(host_model_path, narration_path, host_video_path, fps)
    encode_video(base_path, banner_path, ticker_path, logo_path, host_video_path, ticker_period, narration_path, output_path, fps, width, height, use_nvenc)


def render_assets(base_path: Path, banner_path: Path, ticker_path: Path, logo_path: Path, background_path: Path, headline: str, width: int, height: int) -> int:
    if background_path.exists():
        bg = cover_resize(Image.open(background_path).convert("RGB"), width, height)
    else:
        bg = Image.new("RGB", (width, height), (7, 13, 25))

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, width, height), fill=(0, 0, 0, 54))
    draw.rectangle((0, 0, int(width * 0.53), height), fill=(0, 0, 0, 112))
    draw.rectangle((0, height - BANNER_HEIGHT - 20, width, height), fill=(0, 0, 0, 42))

    label_font = font("barlow_extrabold", 33)
    title_font = font("barlow_extrabold", 82)
    draw.rounded_rectangle((70, 70, 418, 132), radius=0, fill=(222, 12, 30, 255))
    draw.text((95, 84), "BREAKING NEWS", font=label_font, fill=(255, 255, 255, 255))
    draw_wrapped(draw, headline.upper(), (68, 170), title_font, max_width=900, fill=(255, 255, 255, 255), line_spacing=2, max_lines=4)

    base_frame = Image.alpha_composite(bg.convert("RGBA"), overlay)
    base_path.parent.mkdir(parents=True, exist_ok=True)
    base_frame.convert("RGB").save(base_path, "PNG")
    render_static_banner(banner_path, width, height)
    ticker_period = render_ticker_strip(ticker_path, headline.upper(), width, height)
    render_logo_placeholder(logo_path)
    return ticker_period


def render_static_banner(banner_path: Path, width: int, height: int) -> None:
    banner = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(banner)
    banner_top = height - BANNER_HEIGHT
    draw.rectangle((0, banner_top - 8, width, banner_top), fill=(9, 18, 35, 190))
    draw.rectangle((0, banner_top, width, height), fill=(246, 248, 251, 255))
    draw.rectangle((0, banner_top, width, banner_top + 8), fill=(214, 14, 32, 255))
    draw.rectangle((0, banner_top + 8, width, banner_top + 10), fill=(15, 32, 58, 46))
    banner_path.parent.mkdir(parents=True, exist_ok=True)
    banner.save(banner_path, "PNG")


def render_live2d_host_video(model_path: Path, narration_path: Path, output_path: Path, fps: int) -> None:
    if not model_path.exists():
        raise FileNotFoundError(f"missing Live2D model: {model_path}")
    try:
        import glfw
        import live2d.v3 as live2d
        import numpy as np
        from OpenGL.GL import GL_COLOR_BUFFER_BIT, GL_RGBA, GL_UNSIGNED_BYTE, glClear, glClearColor, glFinish, glReadPixels, glViewport
    except ImportError as error:
        raise RuntimeError("Live2D rendering requires live2d-py, glfw, PyOpenGL, and numpy") from error

    envelope, duration = load_audio_envelope(narration_path, fps, np)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = subprocess.Popen(
        [
            FFMPEG_PATH,
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{HOST_WIDTH}x{HOST_HEIGHT}",
            "-r", str(fps),
            "-i", "pipe:0",
            "-an",
            "-c:v", "ffv1",
            "-level", "3",
            "-pix_fmt", "bgra",
            str(output_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    window = None
    model = None
    try:
        if not glfw.init():
            raise RuntimeError("GLFW initialization failed for Live2D renderer")
        glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 2)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 1)
        window = glfw.create_window(HOST_WIDTH, HOST_HEIGHT, "hdt-live2d-render", None, None)
        if not window:
            raise RuntimeError("GLFW hidden window creation failed for Live2D renderer")
        glfw.make_context_current(window)
        live2d.init()
        live2d.glInit()

        model = live2d.LAppModel()
        model.LoadModelJson(str(model_path))
        model.Resize(HOST_WIDTH, HOST_HEIGHT)
        model.SetScale(HOST_MODEL_SCALE)
        model.SetOffset(HOST_MODEL_OFFSET_X, HOST_MODEL_OFFSET_Y)
        model.SetAutoBlinkEnable(True)
        model.SetAutoBreathEnable(True)

        glViewport(0, 0, HOST_WIDTH, HOST_HEIGHT)
        for frame_index, mouth in enumerate(envelope):
            t = frame_index / fps
            drive_live2d_parameters(model, float(mouth), t)
            model.Update()
            glClearColor(0.0, 0.0, 0.0, 0.0)
            glClear(GL_COLOR_BUFFER_BIT)
            model.Draw()
            glFinish()
            pixels = glReadPixels(0, 0, HOST_WIDTH, HOST_HEIGHT, GL_RGBA, GL_UNSIGNED_BYTE)
            ffmpeg.stdin.write(flip_rgba_rows(pixels, HOST_WIDTH, HOST_HEIGHT))
    finally:
        if ffmpeg.stdin:
            ffmpeg.stdin.close()
        stdout = ffmpeg.stdout.read() if ffmpeg.stdout else b""
        stderr = ffmpeg.stderr.read() if ffmpeg.stderr else b""
        exit_code = ffmpeg.wait()
        if model is not None and hasattr(model, "DestroyRenderer"):
            model.DestroyRenderer()
        try:
            live2d.dispose()
        except Exception:
            pass
        if window is not None:
            glfw.destroy_window(window)
        glfw.terminate()

    if exit_code != 0:
        raise RuntimeError((stderr or stdout).decode("utf-8", errors="replace") or "Live2D host ffmpeg encode failed")
    if duration <= 0:
        raise RuntimeError("narration WAV duration is zero")
    if not output_path.exists():
        raise RuntimeError("Live2D host render completed but no host video was created")


def drive_live2d_parameters(model, mouth: float, t: float) -> None:
    values = (
        ("ParamMouthOpenY", min(1.0, max(0.0, mouth))),
        ("ParamMouthForm", 0.18 + math.sin(t * 5.0) * 0.12),
        ("ParamAngleX", math.sin(t * 0.72) * 7.0 + math.sin(t * 0.19) * 2.0),
        ("ParamAngleY", math.sin(t * 0.51 + 1.2) * 4.0),
        ("ParamAngleZ", math.sin(t * 0.43 + 0.5) * 5.0),
        ("ParamBodyAngleX", math.sin(t * 0.28 + 0.4) * 4.0),
        ("ParamBodyAngleY", math.sin(t * 0.31 + 1.0) * 2.0),
        ("ParamBodyAngleZ", math.sin(t * 0.36) * 3.0),
        ("ParamBreath", 0.5 + math.sin(t * 1.8) * 0.5),
        ("ParamEyeBallX", math.sin(t * 0.37) * 0.25),
        ("ParamEyeBallY", math.sin(t * 0.29 + 0.5) * 0.18),
    )
    for param_id, value in values:
        try:
            model.SetParameterValue(param_id, float(value))
        except Exception:
            pass


def load_audio_envelope(narration_path: Path, fps: int, np) -> tuple[list[float], float]:
    if not narration_path.exists():
        raise FileNotFoundError(f"missing narration wav: {narration_path}")
    with wave.open(str(narration_path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        frames = wav.getnframes()
        raw = wav.readframes(frames)
    if sample_width != 2:
        raise RuntimeError(f"expected 16-bit PCM WAV for lip sync, got sample width {sample_width}")
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    duration = len(audio) / sample_rate if sample_rate else 0
    frame_count = max(1, math.ceil(duration * fps))
    samples_per_frame = sample_rate / fps
    rms = []
    for index in range(frame_count):
        start = int(index * samples_per_frame)
        end = min(len(audio), int((index + 1) * samples_per_frame))
        segment = audio[start:end]
        rms.append(0.0 if len(segment) == 0 else float(np.sqrt(np.mean((segment / 32768.0) ** 2))))
    peak = max(rms) or 1.0
    normalized = [min(1.0, value / (peak * 0.62)) for value in rms]
    smoothed = []
    current = 0.0
    for value in normalized:
        factor = 0.55 if value > current else 0.22
        current = current + (value - current) * factor
        smoothed.append(min(1.0, max(0.0, current * 1.18)))
    return smoothed, duration


def encode_video(base_path: Path, banner_path: Path, ticker_path: Path, logo_path: Path, host_video_path: Path, ticker_period: int, narration_path: Path, output_path: Path, fps: int, width: int, height: int, use_nvenc: bool) -> None:
    if not narration_path.exists():
        raise FileNotFoundError(f"missing narration wav: {narration_path}")
    if not host_video_path.exists():
        raise FileNotFoundError(f"missing Live2D host video: {host_video_path}")
    banner_top = height - BANNER_HEIGHT
    ticker_left = BANNER_MARGIN_X
    ticker_y = banner_top + 12
    logo_x = width - BANNER_MARGIN_X - LOGO_WIDTH
    logo_y = banner_top
    host_x = width - HOST_WIDTH - HOST_MARGIN_RIGHT
    host_y = height - BANNER_HEIGHT - HOST_HEIGHT + HOST_BASELINE_OVERLAP
    scroll = f"{ticker_left}-mod(t*{TICKER_SPEED}\\,{ticker_period})"
    filter_complex = ";".join([
        f"[0:v][4:v]overlay=x={host_x}:y={host_y}:format=auto[hosted]",
        "[hosted][2:v]overlay=x=0:y=0:format=auto[with_banner]",
        f"[with_banner][3:v]overlay=x='{scroll}':y={ticker_y}:format=auto[with_ticker]",
        f"[with_ticker][5:v]overlay=x={logo_x}:y={logo_y}:format=auto[v]",
    ])
    command = [
        FFMPEG_PATH,
        "-y",
        "-loop", "1",
        "-framerate", str(fps),
        "-i", str(base_path),
        "-i", str(narration_path),
        "-loop", "1",
        "-framerate", str(fps),
        "-i", str(banner_path),
        "-loop", "1",
        "-framerate", str(fps),
        "-i", str(ticker_path),
        "-i", str(host_video_path),
        "-loop", "1",
        "-framerate", str(fps),
        "-i", str(logo_path),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "1:a",
        *video_encoder_args(use_nvenc),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        return
    if use_nvenc:
        encode_video(base_path, banner_path, ticker_path, logo_path, host_video_path, ticker_period, narration_path, output_path, fps, width, height, False)
        return
    raise RuntimeError(result.stderr or result.stdout or "ffmpeg failed")


def video_encoder_args(use_nvenc: bool) -> list[str]:
    if use_nvenc:
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "18"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]


def render_ticker_strip(ticker_path: Path, headline: str, width: int, height: int) -> int:
    ticker_font = font("barlow_semibold", 58)
    ticker_height = BANNER_HEIGHT - 18
    left = BANNER_MARGIN_X
    logo_x = width - BANNER_MARGIN_X - LOGO_WIDTH
    ticker_width = logo_x - left - LOGO_GAP
    gap = 150
    measure = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    bbox = ImageDraw.Draw(measure).textbbox((0, 0), headline, font=ticker_font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    period = text_width + gap
    strip_width = ticker_width + period + gap
    strip = Image.new("RGBA", (strip_width, ticker_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(strip)
    y = (ticker_height - text_height) // 2 - bbox[1] + 3
    dot_size = 18
    dot_y = round(y + bbox[1] + text_height / 2 - dot_size / 2)
    x = 0
    while x < strip_width:
        draw.text((x, y), headline, font=ticker_font, fill=(10, 22, 40, 255))
        dot_x = x + text_width + 54
        draw.rectangle((dot_x, dot_y, dot_x + dot_size, dot_y + dot_size), fill=(214, 14, 32, 255))
        x += period
    ticker_path.parent.mkdir(parents=True, exist_ok=True)
    strip.save(ticker_path, "PNG")
    return period


def render_logo_placeholder(logo_path: Path) -> None:
    logo = Image.new("RGBA", (LOGO_WIDTH + BANNER_MARGIN_X, BANNER_HEIGHT), (246, 248, 251, 255))
    draw = ImageDraw.Draw(logo)
    logo_font = font("archivo", 42)
    draw.line((0, 32, 0, BANNER_HEIGHT - 32), fill=(154, 164, 178, 255), width=2)
    bbox = draw.textbbox((0, 0), "LOGO", font=logo_font)
    x = (LOGO_WIDTH - (bbox[2] - bbox[0])) // 2
    y = (BANNER_HEIGHT - (bbox[3] - bbox[1])) // 2 - bbox[1] + 1
    draw.text((x, y), "LOGO", font=logo_font, fill=(10, 22, 40, 255))
    logo_path.parent.mkdir(parents=True, exist_ok=True)
    logo.save(logo_path, "PNG")


def cover_resize(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = max(width / image.width, height / image.height)
    size = (math.ceil(image.width * scale), math.ceil(image.height * scale))
    resized = image.resize(size, Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def draw_wrapped(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], image_font: ImageFont.FreeTypeFont, max_width: int, fill, line_spacing: int, max_lines: int) -> None:
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=image_font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
        lines[-1] = lines[-1].rstrip(".,;:") + "..."
    x, y = xy
    line_height = image_font.size + line_spacing
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_height), line, font=image_font, fill=fill)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    candidates = {
        "barlow_semibold": [FONT_ROOT / "BarlowCondensed" / "BarlowCondensed-SemiBold.ttf"],
        "barlow_extrabold": [FONT_ROOT / "BarlowCondensed" / "BarlowCondensed-ExtraBold.ttf"],
        "archivo": [FONT_ROOT / "Archivo" / "Archivo.ttf"],
        "inter_semibold": [FONT_ROOT / "Inter" / "Inter.ttf"],
    }.get(name, [])
    candidates.extend([
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ])
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def flip_rgba_rows(pixels: bytes, width: int, height: int) -> bytes:
    stride = width * 4
    return b"".join(pixels[row * stride:(row + 1) * stride] for row in range(height - 1, -1, -1))
