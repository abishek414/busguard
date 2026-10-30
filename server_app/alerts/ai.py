"""
The AI part (runs on Azure). Gives one photo a "fight score" from 0.0 to 1.0.

The bus camera sends a small photo about once a second. For each photo:
* If models/fight_cls.pt exists (the trained fight model), it says how likely
  the photo shows a fight.
* If not, a simple placeholder rule is used (YOLO person detector: two people
  overlapping). It is only there so the system can be tested before training.
"""
import logging
import os
import shutil
import subprocess
import threading

from django.conf import settings

log = logging.getLogger("alerts")
_load_lock = threading.Lock()
_predict_lock = threading.Lock()  # YOLO models are not safe to call from two threads at once
_models = {}


def _load(name):
    """Load a model once and keep it in memory. If the file changes (after a
    'Retrain AI' run swaps in a new model), the new version is loaded automatically."""
    with _load_lock:
        stamp = os.path.getmtime(name) if os.path.exists(name) else None
        cached = _models.get(name)
        if cached is None or cached[1] != stamp:
            from ultralytics import YOLO  # imported here so Django starts fast
            log.info("Loading model %s", name)
            _models[name] = (YOLO(name), stamp)
        return _models[name][0]


def model_ready():
    return os.path.exists(str(settings.FIGHT_MODEL_PATH))


def _boxes_overlap(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return min(ax2, bx2) > max(ax1, bx1) and min(ay2, by2) > max(ay1, by1)


def score_frame(image):
    """image = OpenCV BGR picture. Returns (score 0-1, short note)."""
    model_path = str(settings.FIGHT_MODEL_PATH)
    if os.path.exists(model_path):
        model = _load(model_path)
        fight_idx = next((i for i, name in model.names.items()
                          if name.lower() in ("fight", "violence")), 0)
        with _predict_lock:
            result = model(image, verbose=False)[0]
        score = float(result.probs.data[fight_idx])
        return score, "Fight model"

    # Placeholder rule until the fight model is trained
    detector = _load("yolov8n.pt")
    with _predict_lock:
        result = detector(image, classes=[0], conf=0.4, verbose=False)[0]
    boxes = result.boxes.xyxy.cpu().numpy().tolist()
    overlapping = any(_boxes_overlap(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:])
    score = 0.7 if overlapping else 0.05 + 0.05 * min(len(boxes), 5)
    return score, f"Placeholder rule (no fight model yet): {len(boxes)} people"


def ffmpeg_path():
    """ffmpeg converts clips so browsers can play them. Use the copy that comes with the
    imageio-ffmpeg package (pip install), or one installed on the computer."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def to_browser_mp4(path):
    """OpenCV saves mp4v, which phones/browsers can't play. Convert to H.264 with ffmpeg."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        log.warning("ffmpeg not found (pip install imageio-ffmpeg) - video won't play in the browser")
        return path
    out = os.path.splitext(path)[0] + "_h264.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", path, "-c:v", "libx264", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
         "-movflags", "+faststart", "-an", out],
        check=True, timeout=180,
    )
    os.remove(path)
    return out
