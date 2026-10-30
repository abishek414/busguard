# BusGuard Darwin

AI that spots fights on buses and at bus stations, and gets the nearest officer there fast.

1. A **camera** (the bus's CCTV, or a phone used as the camera) sends a small photo about once a second.
2. The **AI on the server** scores each photo. When it sees a fight in several photos in a row, the
   camera sends a **20-second clip**.
3. A person in the **control room** checks the clip, sees the bus on a live map, and sends it to the
   **nearest field officer**.
4. The **officer's phone** beeps and shows the bus's live position and directions. The incident stays
   open until the officer taps **Resolved**.
5. Every handled incident teaches the AI, which can **retrain itself** on the server.

| Folder | What it is |
|---|---|
| [`server_app`](server_app/README.md) | Django website + AI: control room, officer screen, phone camera page, manager pages |
| [`camera_app`](camera_app/README.md) | Small program for a CCTV camera (runs next to the camera) |

Made with Python, Django, YOLOv8 (image classification), Leaflet maps and OpenStreetMap.
