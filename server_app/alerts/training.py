"""
Self-learning: the server retrains its own fight detector from real bus incidents.

Where the "correct answers" come from (no extra work for anyone):
  * checker dismisses an incident        -> its clip shows "normal" (a false alarm)
  * officer resolves it: "real fight"    -> "fight"
  * officer resolves it: "not a fight"   -> "normal"

How a retrain works (alerts/management/commands/retrain_model.py runs this):
  1. Take 8 photos from every labelled incident clip.
  2. Mix them with a sample of the ORIGINAL training photos (training_data/base),
     so the AI doesn't forget what it already knows.
  3. Test the CURRENT model on the test photos  -> old accuracy.
  4. Train a few rounds, starting from the current model (not from zero).
  5. Test the NEW model on exactly the same test photos -> new accuracy.
  6. Only if the new model is at least as good: back up the old one and switch.

Folders (inside TRAINING_DIR, default server_app/training_data):
  base/train|val/fight|normal/   original photos  (made once by build_base_dataset)
  incidents/<id>/                photos taken from each incident clip
  work/                          the combined dataset for the current run
  runs/                          training output
"""
import logging
import os
import random
import shutil

from django.conf import settings
from django.utils import timezone

from .models import Incident, TrainingRun

log = logging.getLogger("alerts")

IMG = 224
FRAMES_PER_CLIP = 8
FIGHT_NAMES = {"violence", "voilence", "fight", "fights", "violent"}
NORMAL_NAMES = {"nonviolence", "non-violence", "non_violence", "non violence", "nonvoilence",
                "non-voilence", "non_voilence", "non voilence", "nonfight", "non-fight",
                "non_fight", "normal"}
VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"}


def training_dir():
    return settings.TRAINING_DIR


def base_ready():
    base = training_dir() / "base"
    return all((base / split / label).is_dir() and any((base / split / label).iterdir())
               for split in ("train", "val") for label in ("fight", "normal"))


def extract_frames(video_path, out_dir, prefix, count):
    """Save `count` evenly spaced photos from a video (skipping the first/last 10%)."""
    import cv2
    os.makedirs(out_dir, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    saved = 0
    if total > 0:
        start, end = int(total * 0.1), max(int(total * 0.9), 1)
        step = max((end - start) // count, 1)
        for i in range(count):
            cap.set(cv2.CAP_PROP_POS_FRAMES, start + i * step)
            ok, frame = cap.read()
            if ok:
                cv2.imwrite(os.path.join(out_dir, f"{prefix}_{i}.jpg"), cv2.resize(frame, (IMG, IMG)))
                saved += 1
    cap.release()
    return saved


# ---------- Original photos (made once) ----------

def find_videos(root):
    """Fight/normal videos in a dataset folder, with duplicates removed."""
    found, seen = {"fight": [], "normal": []}, set()
    for dirpath, _, names in sorted(os.walk(root)):
        folder = os.path.basename(dirpath).lower()
        label = "fight" if folder in FIGHT_NAMES else "normal" if folder in NORMAL_NAMES else None
        if not label:
            continue
        for n in sorted(names):
            path = os.path.join(dirpath, n)
            key = (label, n.lower(), os.path.getsize(path))
            if os.path.splitext(n)[1].lower() in VIDEO_EXT and key not in seen:
                seen.add(key)
                found[label].append(path)
    return found


def build_base_dataset(dataset_path, videos_per_class=150, frames=4, echo=print):
    base = training_dir() / "base"
    shutil.rmtree(base, ignore_errors=True)
    videos = find_videos(dataset_path)
    echo(f"Found {len(videos['fight'])} fight and {len(videos['normal'])} normal videos")
    if not videos["fight"] or not videos["normal"]:
        raise ValueError("Could not find a fight folder and a normal folder in " + str(dataset_path))
    random.seed(42)
    for label, vids in videos.items():
        random.shuffle(vids)
        vids = vids[:videos_per_class]
        cut = int(len(vids) * 0.8)
        for split, subset in (("train", vids[:cut]), ("val", vids[cut:])):
            n = sum(extract_frames(v, base / split / label, f"base_{os.path.splitext(os.path.basename(v))[0]}",
                                   frames) for v in subset)
            echo(f"  {split}/{label}: {n} photos from {len(subset)} videos")


# ---------- Photos from real incidents ----------

def labelled_incidents():
    out = []
    for i in Incident.objects.filter(status__in=[Incident.Status.DISMISSED, Incident.Status.RESOLVED]):
        if i.training_label and i.video and os.path.exists(i.video.path):
            out.append(i)
    return out


def learning_summary():
    """Numbers for the manager's 'AI improvement' page."""
    labelled = [(i.training_label, i.closed_at) for i in Incident.objects.filter(
        status__in=[Incident.Status.DISMISSED, Incident.Status.RESOLVED]) if i.training_label]
    last_done = TrainingRun.objects.exclude(status__in=[TrainingRun.Status.RUNNING,
                                                        TrainingRun.Status.FAILED]).first()
    since = last_done.started_at if last_done else None
    active = TrainingRun.objects.filter(status=TrainingRun.Status.ACTIVATED).first()
    running = TrainingRun.objects.filter(status=TrainingRun.Status.RUNNING).first()
    if running and (timezone.now() - running.started_at).total_seconds() > 3 * 3600:
        running.status, running.error = TrainingRun.Status.FAILED, "Stopped responding"
        running.finished_at = timezone.now()
        running.save()
        running = None
    return {
        "model_version": active.version if active else 1,
        "model_accuracy": round(active.new_accuracy * 100, 1) if active and active.new_accuracy else None,
        "fight": sum(1 for lbl, _ in labelled if lbl == "fight"),
        "normal": sum(1 for lbl, _ in labelled if lbl == "normal"),
        "new_since_last": sum(1 for _, closed in labelled if since is None or (closed and closed > since)),
        "base_ready": base_ready(),
        "running": running.to_dict() if running else None,
        "history": [r.to_dict() for r in TrainingRun.objects.all()[:10]],
    }


# ---------- The retrain itself ----------

def _evaluate(model_path, data_dir):
    from ultralytics import YOLO
    metrics = YOLO(str(model_path)).val(data=str(data_dir), split="val", imgsz=IMG, batch=32,
                                        device="cpu", workers=0, plots=False, verbose=False)
    return float(metrics.top1)


def retrain(run):
    """Run one retrain. Updates `run` as it goes so the control room can show progress."""
    from ultralytics import YOLO

    def step(text):
        run.progress = text[:255]
        run.save(update_fields=["progress"])
        log.info("Retrain #%s: %s", run.pk, text)

    try:
        model_path = settings.FIGHT_MODEL_PATH
        if not os.path.exists(model_path):
            raise RuntimeError("No trained model yet (models/fight_cls.pt).")
        if not base_ready():
            raise RuntimeError("Original photos missing. Run: python manage.py build_base_dataset <dataset folder>")

        # 1. Photos from labelled incidents
        step("Collecting photos from real incidents...")
        work = training_dir() / "work"
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(training_dir() / "base", work)
        counts = {"fight": 0, "normal": 0}
        for inc in labelled_incidents():
            cache = training_dir() / "incidents" / str(inc.pk)
            if not cache.is_dir() or not any(cache.iterdir()):
                extract_frames(inc.video.path, cache, f"incident{inc.pk}", FRAMES_PER_CLIP)
            # Keep each incident's photos together: 1 in 5 incidents is used for testing only
            split = "val" if inc.pk % 5 == 0 else "train"
            label = inc.training_label
            for photo in cache.iterdir():
                shutil.copy(photo, work / split / label / photo.name)
            counts[label] += 1
        run.new_fight, run.new_normal = counts["fight"], counts["normal"]
        run.save(update_fields=["new_fight", "new_normal"])
        if not counts["fight"] and not counts["normal"]:
            raise RuntimeError("No labelled incidents yet. Dismiss or resolve some incidents first.")

        # 2. How good is the current model?
        step(f"Testing the current model ({counts['fight']} fight + {counts['normal']} normal incidents)...")
        run.old_accuracy = _evaluate(model_path, work)
        run.save(update_fields=["old_accuracy"])

        # 3. Train a few rounds, starting from the current model
        epochs = settings.RETRAIN_EPOCHS
        step(f"Training round 1 of {epochs}...")
        model = YOLO(str(model_path))

        def on_epoch_end(trainer):
            done = trainer.epoch + 1
            if done < trainer.epochs:
                step(f"Training round {done + 1} of {trainer.epochs}...")
        model.add_callback("on_fit_epoch_end", on_epoch_end)
        model.train(data=str(work), epochs=epochs, imgsz=IMG, batch=32, device="cpu", workers=0,
                    project=str(training_dir() / "runs"), name=f"run{run.pk}", exist_ok=True,
                    plots=False, verbose=False, patience=epochs)
        best = model.trainer.best if os.path.exists(model.trainer.best) else model.trainer.last

        # 4. Same test, new model
        step("Testing the new model...")
        run.new_accuracy = _evaluate(best, work)

        # 5. Switch only if it's at least as good
        if run.new_accuracy >= run.old_accuracy:
            active = TrainingRun.objects.filter(status=TrainingRun.Status.ACTIVATED).first()
            current_version = active.version if active else 1
            history = model_path.parent / "history"
            os.makedirs(history, exist_ok=True)
            shutil.copy(model_path, history / f"fight_cls_v{current_version}.pt")   # backup
            shutil.copy(best, model_path)
            run.version = current_version + 1
            run.status = TrainingRun.Status.ACTIVATED
            run.progress = f"Model v{run.version} activated"
        else:
            run.status = TrainingRun.Status.KEPT_OLD
            run.progress = "New model was not better - kept the old one"
    except Exception as e:
        log.exception("Retrain #%s failed", run.pk)
        run.status = TrainingRun.Status.FAILED
        run.error = str(e)
        run.progress = "Failed: " + str(e)[:200]
    run.finished_at = timezone.now()
    run.save()
    shutil.rmtree(training_dir() / "work", ignore_errors=True)
    return run
