"""
BusGuard - camera program for a CCTV camera on a bus or at a station.

It runs on the small computer next to the camera. It has NO AI of its own:

  1. It reads the camera's live stream (the address is saved on the manager's
     Buses & areas page, e.g. rtsp://192.168.1.10:554/stream1).
  2. About once a second (when there is movement) it sends a small photo to
     the BusGuard server on Azure.
  3. Azure's AI scores the photo and replies "normal" or "fight".
  4. When Azure has seen a fight in several photos in a row, it replies
     record=true. The camera then sends a 20-second clip (including the
     3 seconds before), which pops up in the control room.

GPS doesn't come from here: a bus's position comes from its GPS tracker (fleet
GPS feed) and a station has a fixed location, both set on the manager's page.
"""
import argparse
import collections
import os
import threading
import time

import cv2
import requests

def parse_args():
    p = argparse.ArgumentParser(description="BusGuard bus camera (sends photos to Azure)")
    p.add_argument("--source", default=None,
                   help="0 = webcam, a video file, or a camera stream (rtsp://...). "
                        "With --key, the camera address saved for the bus is used if you leave this out.")
    p.add_argument("--server", default=os.environ.get("BUSGUARD_SERVER", "http://127.0.0.1:8000"))
    p.add_argument("--key", default=os.environ.get("BUS_KEY", ""),
                   help="the bus's key from the manager's Connect page")
    p.add_argument("--clip-seconds", type=int, default=20)
    p.add_argument("--motion", type=float, default=4.0,
                   help="how much change counts as movement (lower = more photos sent)")
    p.add_argument("--idle-every", type=float, default=5.0,
                   help="seconds between photos when nothing is moving")
    p.add_argument("--no-window", action="store_true", help="don't show the camera window")
    args = p.parse_args()
    if not args.key:
        p.error("--key is required: copy it from the manager's Buses & areas page (Connect)")
    return args


# ---------- Talking to Azure (in a background thread so the video never freezes) ----------

class AzureLink:
    def __init__(self, args):
        self.args = args
        self.url = args.server.rstrip("/")
        self.headers = {"X-Bus-Key": args.key}
        self.session = requests.Session()
        self.lock = threading.Lock()
        self.pending = None
        self.result = {"text": "Connecting to Azure...", "colour": (200, 200, 200)}
        self.record_requested = threading.Event()
        self.upload_status = ""
        threading.Thread(target=self._loop, daemon=True).start()

    def send_photo(self, jpeg_bytes):
        with self.lock:
            self.pending = jpeg_bytes   # only the newest photo matters

    def _loop(self):
        while True:
            with self.lock:
                job, self.pending = self.pending, None
            if not job:
                time.sleep(0.05)
                continue
            try:
                r = self.session.post(f"{self.url}/api/frame/", headers=self.headers, timeout=15,
                                      files={"image": ("frame.jpg", job, "image/jpeg")})
                if r.status_code != 200:
                    self.result = {"text": f"Azure error {r.status_code}: {r.text[:60]}", "colour": (0, 165, 255)}
                    continue
                d = r.json()
                if d["fight"]:
                    self.result = {"text": f"Azure AI: POSSIBLE FIGHT {d['score']}%  ({d['streak']} in a row)",
                                   "colour": (0, 0, 255)}
                else:
                    self.result = {"text": f"Azure AI: normal {d['score']}%", "colour": (0, 200, 0)}
                if d["record"]:
                    self.record_requested.set()
            except requests.RequestException as e:
                self.result = {"text": f"Can't reach Azure: {type(e).__name__}", "colour": (0, 165, 255)}
                time.sleep(1)

    def upload_clip(self, path, manual):
        def run():
            self.upload_status = "Sending clip to control room..."
            try:
                with open(path, "rb") as f:
                    r = self.session.post(f"{self.url}/api/upload/", headers=self.headers, timeout=180,
                                          data={"manual": "1" if manual else "0"},
                                          files={"video": (os.path.basename(path), f, "video/mp4")})
                ok = r.status_code == 201
                self.upload_status = "Clip sent to control room" if ok else f"Clip upload failed ({r.status_code})"
                print(f"[clip] {self.upload_status}: {r.text[:100]}")
            except requests.RequestException as e:
                self.upload_status = f"Clip upload failed: {type(e).__name__} (kept in {path})"
                print(f"[clip] {self.upload_status}")
        threading.Thread(target=run, daemon=True).start()


# ---------- Main loop ----------

def small(frame, width):
    h, w = frame.shape[:2]
    return frame if w <= width else cv2.resize(frame, (width, int(h * width / w)))


def main():
    args = parse_args()
    # Ask the server for this bus's name and camera address
    try:
        r = requests.get(f"{args.server.rstrip('/')}/api/bus/config/", headers={"X-Bus-Key": args.key}, timeout=15)
    except requests.RequestException as e:
        raise SystemExit(f"Can't reach the server at {args.server}: {e}")
    if r.status_code != 200:
        try:
            reason = r.json().get("error", r.text)
        except ValueError:
            reason = r.text[:200]
        raise SystemExit(f"Server refused this bus: {reason}")
    cfg = r.json()
    args.bus = cfg["name"]
    print(f"Connected as '{args.bus}'")
    # Camera address saved for this bus by the system manager (used unless --source is given)
    if args.source is None:
        if cfg.get("camera_type") == "phone":
            raise SystemExit(f"{args.bus} uses a phone as its camera. Open the phone camera page "
                             "(link on the manager's Connect page), or add --source to use this computer.")
        args.source = cfg.get("camera_address") or "0"

    source = int(args.source) if args.source.isdigit() else args.source
    is_file = isinstance(source, str) and os.path.isfile(source)
    print(f"Camera: {args.source}")
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise SystemExit(f"Could not open the camera: {args.source}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps < 1 or fps > 60:
        fps = 25.0
    link = AzureLink(args)
    os.makedirs("clips", exist_ok=True)

    before = collections.deque(maxlen=int(fps * 3))   # last 3 seconds, for the clip
    prev_small = None
    last_sent = 0.0
    writer, frames_left, clip_path, clip_manual = None, 0, None, False
    show = not args.no_window

    print(f"{args.bus}: sending photos to {link.url}  (press a = alert, q = quit)")
    while True:
        started = time.time()
        ok, frame = cap.read()
        if not ok:
            if is_file:                               # a video file plays on repeat
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            print("Camera stopped.")
            break
        frame = small(frame, 640)
        before.append(frame)

        # Is anything moving? (cheap check, no AI)
        tiny = cv2.GaussianBlur(cv2.cvtColor(small(frame, 160), cv2.COLOR_BGR2GRAY), (5, 5), 0)
        motion = 0.0 if prev_small is None else float(cv2.absdiff(tiny, prev_small).mean())
        prev_small = tiny

        # Send a small photo: every second while there's movement, otherwise every few seconds
        now = time.time()
        interval = 1.0 if motion >= args.motion else args.idle_every
        if now - last_sent >= interval:
            ok_jpg, jpeg = cv2.imencode(".jpg", small(frame, 320), [cv2.IMWRITE_JPEG_QUALITY, 60])
            if ok_jpg:
                link.send_photo(jpeg.tobytes())
                last_sent = now

        # Keys
        key = -1
        if show:
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break

        # Start recording when Azure asks for a clip (or 'a' is pressed)
        if writer is None and (link.record_requested.is_set() or key == ord("a")):
            clip_manual = not link.record_requested.is_set()
            link.record_requested.clear()
            clip_path = os.path.join("clips", time.strftime("clip_%Y%m%d_%H%M%S.mp4"))
            h, w = frame.shape[:2]
            writer = cv2.VideoWriter(clip_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
            for f in before:
                writer.write(f)
            frames_left = int(fps * args.clip_seconds) - len(before)
            print(f"[clip] recording ({'manual alert' if clip_manual else 'requested by Azure AI'})")
        elif writer is not None:
            writer.write(frame)
            frames_left -= 1
            if frames_left <= 0:
                writer.release()
                writer = None
                link.upload_clip(clip_path, clip_manual)

        if show:
            view = frame.copy()
            res = link.result
            cv2.rectangle(view, (0, 0), (view.shape[1], 58), (0, 0, 0), -1)
            cv2.putText(view, res["text"], (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, res["colour"], 2)
            cv2.putText(view, f"{args.bus}   motion {motion:.1f}",
                        (10, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
            if writer is not None:
                cv2.circle(view, (view.shape[1] - 30, 85), 10, (0, 0, 255), -1)
                cv2.putText(view, "REC", (view.shape[1] - 80, 91), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            if link.upload_status:
                cv2.putText(view, link.upload_status, (10, view.shape[0] - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
            cv2.imshow(f"BusGuard camera - {args.bus}", view)

        # Play video files at normal speed (webcams already run at their own speed)
        if is_file:
            time.sleep(max(0.0, 1.0 / fps - (time.time() - started)))

    if writer is not None:
        writer.release()
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
