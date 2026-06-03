from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_ROOT = PROJECT_ROOT / "assets"
DEFAULT_BACKGROUND_PATH = ASSET_ROOT / "backgrounds" / "studio.png"
DEFAULT_HOST_MODEL_PATH = ASSET_ROOT / "live2d" / "chodenpa_student" / "VT_student.model3.json"
DEFAULT_JOBS_ROOT = PROJECT_ROOT / "jobs"
