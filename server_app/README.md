# server_app - BusGuard server (Django + AI)

This is the "Azure" part of BusGuard. It does four jobs:

1. **AI on every photo.** Buses send a small photo about once a second (`/api/frame/`).
   The AI scores it. After **3 "fight" photos in a row**, it tells the bus to send a
   20-second clip (`/api/upload/`), which becomes an **incident**.
2. **Control room** (`/control/`), for the checker who always has it open:
   - stats bar (open now, today, resolved, average officer response time)
   - **live map**: buses driving (green / red / orange), officers (blue / orange), incidents
   - **Live buses** panel: each bus's latest photo, AI score, online/offline
   - new incident: **beep + banner + pop-up notification**, clip plays, live bus GPS
   - **officers sorted by distance from where the bus is now**, with Send and Call buttons,
     and a line on the map to the nearest free officer
   - **timeline**: reported → sent → on the way → resolved, with response time
   - **no-reply alarm**: if the officer doesn't tap "On my way" within 60 seconds, the control
     room beeps again and offers to **re-send to the next nearest officer**
   - the incident **stays active until the officer taps Resolved** (or the checker dismisses
     it as a false alarm)
   - **Closed in the last 24 hours** list; older incidents are on the manager's history page
3. **Officer screen** (`/officer/`), on officers' phones:
   - shares their GPS
   - new job: **beep + vibration + pop-up notification**
   - map of them and the bus **as it moves**, live distance, live directions link
   - **On my way** and **Resolved** (with a short report)
4. **Self-learning AI.** Every incident a person handles becomes a lesson for the AI:
   - checker **dismisses** it → the clip shows *normal* (a false alarm)
   - officer taps **Resolved: real fight** / **Resolved: not a fight** → *fight* / *normal*
   - the system manager's **AI improvement** page counts these examples and has a
     **Retrain AI** button. The server then retrains the model by itself (no Colab), tests the
     old and new model on the same test photos, and **only switches if the new one is at least
     as good** (the old model is backed up in `models/history/`).
5. **System manager** (`/manage/`), for the person who runs the system:
   - **Accounts:** create, edit, delete, reset passwords, set each person's role
     (System manager / Control room checker / Field officer)
   - **Buses & areas:** add, edit, delete and switch off a **bus** or a **station / area** (e.g. an
     interchange with its own CCTV). Each one's page has everything to connect it:
     - **Camera:** a **CCTV camera** (its live stream address, e.g. `rtsp://192.168.1.10:554/stream1`; the
       camera program reads it from the server, so it only needs the key), or a **phone used as the camera**.
     - **GPS:** a phone camera always sends the **phone's own GPS**. A CCTV camera uses the **bus GPS tracker**
       (its tracker ID, read through the operator's fleet GPS feed, standard GTFS-Realtime, address entered
       right there) or a **fixed location** for a station (no GPS needed).
     - **Connect** page: the key, the camera command or phone link, and a **Test GPS** button.
   - **Incident history:** every incident ever reported, with search (bus, area, officer, note),
     filters (open, real fight, false alarm, manual alert, dates), a page per incident with its clip,
     notes and timeline, and **Download as spreadsheet (CSV)**
   - **AI improvement:** examples checked by people and the **Retrain AI** button
   - the manager can also open the control room
6. **Phone camera page** (`/cam/<bus key>/`): open it on an iPhone or Android phone and the
   phone **becomes the bus camera**, with no app to install. It sends a small photo (and the phone's GPS) every
   second, records the 20-second clip when the AI asks (starting a few seconds *before* the alert), and
   has a big **Send alert now** button. The manager's **Connect** page has the link. Phones only allow the
   camera and GPS on **https://**, so use it with the Azure address. To use the phone's GPS as the bus's
   position, set the bus's GPS source to **Phone on the bus**.
7. **Django admin** (`/admin/`) for raw database access (needs `python manage.py createsuperuser`).

```
server_app/
  manage.py
  .env.example          <- copy to .env and fill in
  requirements.txt
  busguard/             <- Django settings + urls
  alerts/               <- the app
    models.py           <- Bus, Incident, Profile (checker/officer + GPS)
    views.py            <- bus API, control room, officer screen
    ai.py               <- the AI (scores one photo)
    geo.py              <- distance between two GPS points
    tests.py            <- automatic tests (python manage.py test alerts)
    management/commands/  <- setup_accounts, clear_test_data, retrain_model, ...
    templates/alerts/   <- control.html, officer.html, phone_camera.html
    static/alerts/      <- control.js, officer.js, phone_camera.js, map.js, common.js, css, Leaflet
  templates/            <- base.html, login page
  models/               <- put fight_cls.pt here after training
  training/             <- train_fight_model.ipynb (run on Google Colab)
```

---

## Run it on your Windows computer

Open PowerShell in the `server_app` folder:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py setup_accounts
python manage.py runserver 0.0.0.0:8000
```

(First time only: `py -m venv venv`, then
`pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu` before the
requirements, and `copy .env.example .env`.)

Then open http://127.0.0.1:8000 and log in.

**Starting accounts** (made by `setup_accounts`; password `busguard123` unless you add
`--password YOUR_PASSWORD`):

| Username | Who | Screen |
|---|---|---|
| `manager` | System manager | Accounts, buses & areas, control room |
| `control` | Control room checker | Control room |
| `officer1` | Sam Taylor, field officer | Officer |

Add more officers on the manager's **Accounts** page.

**Add a bus or area:** log in as `manager` → **Buses & areas** → **+ Add bus or area**, choose its
camera (CCTV or phone) and GPS, then **Save and connect**. The Connect page shows the key and:
- **Phone camera:** a link to open on the phone (it needs the https address on Azure).
- **CCTV camera:** the command for the small computer next to the camera:
  ```powershell
  python camera.py --server https://YOUR-SERVER --key THE_BUS_KEY
  ```

**Clear test data** (all incidents, clips and buses/areas; accounts and the AI are kept):
```powershell
python manage.py clear_test_data
```

### Run the automatic tests
```powershell
python manage.py test alerts
```

---

## Settings (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `FIGHT_THRESHOLD` | `0.6` | A photo counts as "fight" at this AI score or above |
| `FIGHT_STREAK` | `3` | "Fight" photos in a row before a clip is requested |
| `ALERT_COOLDOWN` | `60` | Seconds before the same bus can raise another alert |
| `OFFICER_REPLY_SECONDS` | `60` | Seconds before the no-reply alarm |
| `SHOW_LIVE_FRAMES` | `True` | Show each bus's latest photo in the control room |
| `PHONE_CLIP_SECONDS` | `20` | Clip length recorded by the phone camera page |
| `SITE_URL` | `http://127.0.0.1:8000` | Your public https address when hosted |

## The AI

- **Before training** (no `models/fight_cls.pt`), `ai.py` uses a simple **placeholder rule**
  (two people overlapping = 70%). It's only there so you can test everything on day one.
  The control room shows "AI: placeholder rule".
- **After training**, copy `fight_cls.pt` into `models/` and restart the server. The control room
  shows "AI: trained fight model".
- Train it on Google Colab with `training/train_fight_model.ipynb` (about 30-40 minutes, free GPU).

## Self-learning (Retrain AI)

**Once, before the first retrain**, give the server a sample of the original training videos
(the Kaggle dataset you downloaded). Retraining mixes these in so the AI doesn't forget what it
learned on Colab:

```powershell
python manage.py build_base_dataset "C:\Users\Abishek\Documents\data_coding"
```
(Use the folder that contains your fight and normal video folders. Takes a few minutes and
creates `training_data\base`, about 50 MB.)

**Then:**
1. Handle a few incidents: dismiss some, send others to an officer who taps
   **Resolved: real fight** or **Resolved: not a fight**.
2. The manager's **AI improvement** page shows e.g. *"2 real fights, 3 normal, 5 new since the last training"*.
3. Click **Retrain AI**. The panel shows progress (collecting photos → testing the current model →
   training round 1 of 3 → testing the new model), then *"Model v2 activated (91% → 93%)"* or
   *"Kept the old model"*. Takes a few minutes on a normal CPU.
4. The server starts using the new model straight away. No restart needed.

You can also run it yourself: `python manage.py retrain_model`. In a real deployment this would
run on a schedule (e.g. every Sunday night) when few buses are running.

Settings: `RETRAIN_EPOCHS` (default 3 training rounds), `TRAINING_DIR` (default `training_data`).

## Important things to know

- **Click Start first.** Browsers only allow sound after a click, so the control room has
  **Start monitoring** and the officer screen has **Start duty**. Allow notifications if asked.
- **Phones need HTTPS for the camera and GPS.** Use the Azure https address.
  On the server computer itself, `http://127.0.0.1:8000` also works.
- **Keep the officer page open** with the screen on. A website can't beep when the phone is locked.
  (Future improvement: a native app or SMS alerts.)
- **The map needs internet** (map tiles come from OpenStreetMap).
- **Clips are converted** so browsers can play them, using the converter in the `imageio-ffmpeg`
  package (installed by `pip install -r requirements.txt`). Clips saved before that can be fixed with
  `python manage.py fix_clips`.

## Points for the judges

- **Human in the loop:** the AI never contacts officers on its own. A person checks every clip.
- **Gets smarter with use:** every incident handled by people becomes a training example, and the
  server retrains itself, but only switches to a new model when tests show it's at least as good.
  (Real deployment: storing passenger footage for training needs privacy approval, retention rules
  and face blurring.)
- **Nothing gets lost:** an incident stays active until an officer resolves it, and if an officer
  doesn't reply, the control room is warned and re-sends it.
- **Uses existing CCTV:** every Darwin bus already has cameras. BusGuard turns them into live alerts.
- **Privacy:** only small photos are checked, no live video stream, and clips only when the AI
  is fairly sure (or a manual alert). `SHOW_LIVE_FRAMES=False` hides bus photos in the control room.
- **Cost and scale:** about 10 KB per photo, and only when there's movement. For 100 buses:
  one GPU server, PostgreSQL instead of SQLite, and Azure autoscaling.
- **Limits:** the model was trained on public videos (retrain on real bus CCTV), alerts need the
  page open (native app later), and a CCTV camera needs a small computer next to it.
