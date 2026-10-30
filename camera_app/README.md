# camera_app - CCTV camera connector (Python)

Runs on the small computer next to a **CCTV camera** on a bus or at a station.
**It has no AI of its own.** (A phone used as the camera doesn't need this program: it opens
the phone camera page instead.)

1. It reads the camera's live stream (its address, e.g. `rtsp://192.168.1.10:554/stream1`,
   is saved on the manager's **Buses & areas** page).
2. About **once a second** (when something moves) it sends a **small photo** (~10 KB) to the
   BusGuard server.
3. The server's AI scores the photo and replies **normal** or **possible fight**. The answer is
   shown at the top of the camera window.
4. When the AI sees a fight in **3 photos in a row**, the server tells the camera to send a
   **20-second clip** (including the 3 seconds *before*). The clip pops up in the control room.

When nothing is moving, it only sends a photo every 5 seconds, which saves data.

**GPS** doesn't come from here: a bus's position comes from its GPS tracker (through the fleet GPS
feed) and a station has a fixed location. Both are set on the manager's page.

## Set up

It only needs OpenCV and requests (no AI libraries), so it installs in under a minute.

**Windows (PowerShell, in this folder):**
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Mac / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run it

The system manager adds the bus or area (with its CCTV stream address) and clicks **Connect** to
get its key. Then:
```bash
python camera.py --server https://YOUR-SERVER --key THE_BUS_KEY
```
The camera gets its name and stream address from the server.

To test without a CCTV camera, add `--source`: `--source 0` for a webcam, or the path of a video
file (it plays on repeat).

**Keys** (click the camera window first):
- **a**: send an alert clip now (manual alert, like a driver's panic button)
- **q**: quit

## Options

| Option | Default | Meaning |
|---|---|---|
| `--key` | (required) | The bus's key from the manager's Connect page |
| `--server` | `http://127.0.0.1:8000` | BusGuard server address |
| `--source` | the stream saved for the bus | Override: `0` = webcam, a video file, or another `rtsp://...` stream |
| `--clip-seconds` | `20` | Length of each clip |
| `--motion` | `4` | Movement needed to send a photo every second (lower = more photos) |
| `--idle-every` | `5` | Seconds between photos when nothing moves |
| `--no-window` | off | Run without showing the camera window |

## What the window shows

- Top line: **Azure AI: normal 6%** (green) or **POSSIBLE FIGHT 87% (2 in a row)** (red)
- Second line: name and movement level
- **REC** while recording a clip, then "Clip sent to control room"

Clips are also saved in the `clips` folder, so nothing is lost if an upload fails.
