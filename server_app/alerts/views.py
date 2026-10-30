"""
BusGuard views.

Three groups:
  1. Bus camera API         (the bus sends photos and clips here; Azure's AI decides)
  2. Control room           (the checker watches live alerts)
  3. Officer                (field officers on their phones)

The two screens are normal Django pages. A little JavaScript on each page asks
the *_feed endpoints "anything new?" every few seconds (called polling).
"""
import logging
import os
import threading
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import close_old_connections
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from . import ai, gps_feed, training
from .geo import distance_km
from .models import Bus, Incident, Profile, TrainingRun

log = logging.getLogger("alerts")
LOCATION_FRESH_FOR = timedelta(minutes=5)


# ---------- helpers ----------

def role_of(user):
    if user.is_superuser:
        return Profile.Role.MANAGER
    try:
        return user.profile.role
    except Profile.DoesNotExist:
        return None


def role_required(*roles):
    """Only let users with one of these roles in (e.g. officers can't open the control room)."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if role_of(request.user) not in roles:
                return HttpResponseForbidden("You don't have access to this page.")
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


# The system manager can also use the control room
checker_required = role_required(Profile.Role.CHECKER, Profile.Role.MANAGER)
officer_required = role_required(Profile.Role.OFFICER)


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@login_required
def home(request):
    role = role_of(request.user)
    if role == Profile.Role.MANAGER:
        return redirect("alerts:manage")
    if role == Profile.Role.CHECKER:
        return redirect("alerts:control")
    if role == Profile.Role.OFFICER:
        return redirect("alerts:officer")
    return HttpResponseForbidden("Your account has no role yet. Ask an admin to add a Profile.")


# ---------- 1. Bus camera API ----------
#
#  /api/frame/   the bus sends one small photo about every second.
#                Azure's AI scores it and replies. When FIGHT_HITS of the last
#                FIGHT_WINDOW photos look like a fight, the reply says record=true.
#  /api/upload/  the bus then sends the 20-second clip, which becomes an incident.

def bus_from_request(request):
    """Which bus is this? Every camera sends its bus key (X-Bus-Key header).
    Returns (bus, error_response)."""
    key = request.headers.get("X-Bus-Key", "")
    bus = Bus.objects.filter(device_key=key).first() if key else None
    if not bus:
        return None, JsonResponse({"error": "unknown bus key - check the key on the Buses & areas page"}, status=403)
    if not bus.enabled:
        return None, JsonResponse({"error": f"{bus.name} is switched off by the system manager"}, status=403)
    return bus, None


@csrf_exempt
@require_GET
def bus_config(request):
    """The camera computer asks: what's my name, and what's my camera's address?"""
    bus, error = bus_from_request(request)
    return error or JsonResponse(bus.config())


def phone_camera(request, key):
    """Open this page on a phone: the phone becomes the bus camera (and its GPS).
    It sends a photo every second, and records the clip when the AI asks for one."""
    bus = get_object_or_404(Bus, device_key=key)
    return render(request, "alerts/phone_camera.html", {
        "bus": bus,
        "clip_seconds": settings.PHONE_CLIP_SECONDS,
    })


def view_status(note):
    """'blocked' (camera covered, dark or blurry), 'no_people', or 'ok' - from the AI's note."""
    if note.startswith(ai.CAMERA_BLOCKED):
        return "blocked"
    if note.startswith(ai.NO_PEOPLE):
        return "no_people"
    return "ok"


@csrf_exempt
@require_POST
def frame(request):
    bus, error = bus_from_request(request)
    if error:
        return error
    photo = request.FILES.get("image")
    if not photo:
        return JsonResponse({"error": "no image"}, status=400)
    data = photo.read()

    import cv2
    import numpy as np
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return JsonResponse({"error": "could not read image"}, status=400)

    try:
        score, note = ai.score_frame(image)
    except Exception as e:
        log.exception("AI failed on a frame")
        return JsonResponse({"error": f"AI error: {e}"}, status=500)

    name = bus.name
    now = timezone.now()
    bus.set_location_from_device(as_float(request.POST.get("latitude")), as_float(request.POST.get("longitude")))
    bus.last_seen = now
    bus.last_score = score
    bus.last_note = note[:255]

    is_fight = score >= settings.FIGHT_THRESHOLD
    scores = bus.add_score(score, settings.FIGHT_WINDOW)
    fight_scores = [s for s in scores if s >= settings.FIGHT_THRESHOLD]
    hits = len(fight_scores)

    record = False
    cooled_down = (bus.last_alert_at is None or
                   (now - bus.last_alert_at).total_seconds() > settings.ALERT_COOLDOWN)
    # One incident per bus at a time: while this bus already has an open incident
    # (new, sent to an officer or officer on the way), don't raise another one.
    already_open = Incident.objects.filter(bus_id=name, status__in=Incident.ACTIVE).exists()
    if hits >= settings.FIGHT_HITS and cooled_down and not already_open:
        record = True
        bus.last_alert_at = now
        bus.alert_score = max(fight_scores)
        bus.recent_scores = ""          # start counting again after an alert
        log.info("%s: fight in %d of the last %d photos (peak %.0f%%) -> asking for a clip",
                 name, hits, len(scores), bus.alert_score * 100)

    if settings.SHOW_LIVE_FRAMES:
        rel = f"frames/bus_{bus.pk}.jpg"
        path = os.path.join(settings.MEDIA_ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        bus.frame_path = rel
    bus.save()

    return JsonResponse({
        "score": round(score * 100),
        "fight": is_fight,
        "view": view_status(note),          # "ok", "blocked" or "no_people"
        "hits": hits,                       # photos that looked like a fight...
        "window": settings.FIGHT_WINDOW,    # ...out of the last this-many
        "streak": hits,                     # (older name for "hits")
        "record": record,
        "note": note,
    })


@csrf_exempt
@require_POST
def upload(request):
    bus, error = bus_from_request(request)
    if error:
        return error
    video = request.FILES.get("video")
    if not video:
        return JsonResponse({"error": "no video file"}, status=400)

    name = bus.name
    bus.set_location_from_device(as_float(request.POST.get("latitude")), as_float(request.POST.get("longitude")))
    bus.save()
    manual = request.POST.get("manual") == "1"
    if manual:
        score, note = None, "Manual alert sent from the bus"
    elif bus and bus.alert_score is not None:
        score = bus.alert_score
        note = (f"Azure AI saw a fight in {settings.FIGHT_HITS} of the last {settings.FIGHT_WINDOW} photos "
                f"(peak {score:.0%})")
    else:
        score, note = None, "Clip sent by the bus"

    incident = Incident.objects.create(
        bus_id=name,
        latitude=bus.latitude,
        longitude=bus.longitude,
        video=video,
        fight_score=score,
        ai_note=note[:255],
    )
    # Convert the video in the background so the bus isn't kept waiting
    threading.Thread(target=process_incident, args=(incident.pk,), daemon=True).start()
    return JsonResponse({"id": incident.pk, "status": incident.status}, status=201)


def process_incident(pk):
    """Convert the clip so browsers can play it, then show it in the control room."""
    close_old_connections()
    try:
        incident = Incident.objects.get(pk=pk)
        try:
            new_path = ai.to_browser_mp4(incident.video.path)
            incident.video.name = os.path.relpath(new_path, settings.MEDIA_ROOT).replace(os.sep, "/")
            incident.status = Incident.Status.NEW
        except Exception as e:
            # Keep the clip and show it anyway - a human can still check it
            log.exception("Video conversion failed for incident %s", pk)
            incident.status = Incident.Status.ERROR
            incident.ai_note = f"{incident.ai_note} | video problem: {e}"[:255]
        incident.save()
        log.info("Incident %s from %s is ready (%s)", pk, incident.bus_id, incident.status)
    finally:
        close_old_connections()


# ---------- 2. Control room (checker) ----------

@checker_required
@ensure_csrf_cookie
def control_room(request):
    return render(request, "alerts/control.html")


@checker_required
@require_GET
def control_feed(request):
    gps_feed.maybe_poll()   # keep buses on the fleet GPS feed up to date (runs in the background)
    active = (Incident.objects.filter(status__in=Incident.ACTIVE)
              .exclude(status=Incident.Status.ANALYSING, created_at__lt=timezone.now() - timedelta(minutes=10))
              .select_related("assigned_officer__profile"))
    # Closed in the last 24 hours (older ones are on the manager's Incident history page)
    recent = (Incident.objects.exclude(status__in=Incident.ACTIVE)
              .filter(closed_at__gte=timezone.now() - timedelta(hours=24))
              .order_by("-closed_at")
              .select_related("assigned_officer__profile")[:30])
    fresh = timezone.now() - LOCATION_FRESH_FOR
    busy = dict(Incident.objects.filter(status__in=[Incident.Status.ASSIGNED, Incident.Status.ON_WAY])
                .values_list("assigned_officer_id", "pk"))
    officers = []
    for p in Profile.objects.filter(role=Profile.Role.OFFICER).select_related("user"):
        officers.append({
            "id": p.user_id,
            "name": p.display_name,
            "phone": p.phone,
            "latitude": p.latitude,
            "longitude": p.longitude,
            "online": p.is_online(fresh),
            "busy_with": busy.get(p.user_id),
        })
    active = list(active)
    return JsonResponse({
        "incidents": with_live_bus(active),
        "recent": with_live_bus(recent),
        "buses": [b.to_dict(settings.SHOW_LIVE_FRAMES) for b in Bus.objects.all()],
        "officers": officers,
        "officers_on_duty": sum(1 for o in officers if o["online"]),
        "model_ready": ai.model_ready(),
        "stats": today_stats(len(active)),
    })


@role_required(Profile.Role.MANAGER)
@require_POST
def start_retrain(request):
    """'Retrain AI' button: runs `manage.py retrain_model` in the background
    (a separate process, so the website stays fast while it trains)."""
    import subprocess
    import sys
    if TrainingRun.objects.filter(status=TrainingRun.Status.RUNNING).exists():
        return JsonResponse({"error": "A retrain is already running."}, status=409)
    if not training.base_ready():
        return JsonResponse({"error": "Retraining isn't set up on this server yet (original training photos missing)."},
                            status=400)
    if not ai.model_ready():
        return JsonResponse({"error": "No trained model yet (models/fight_cls.pt)."}, status=400)
    run = TrainingRun.objects.create(progress="Starting...")
    os.makedirs(settings.TRAINING_DIR, exist_ok=True)
    logfile = open(settings.TRAINING_DIR / "retrain.log", "a")
    subprocess.Popen([sys.executable, str(settings.BASE_DIR / "manage.py"), "retrain_model", "--run", str(run.pk)],
                     cwd=str(settings.BASE_DIR), stdout=logfile, stderr=subprocess.STDOUT)
    return JsonResponse({"ok": True, "run": run.to_dict()})


@role_required(Profile.Role.MANAGER)
@require_GET
def ai_status(request):
    """Numbers for the manager's AI improvement page (labelled examples, retrain progress)."""
    return JsonResponse({"model_ready": ai.model_ready(), **training.learning_summary()})


def with_live_bus(incidents):
    """Incident data plus where each bus is right now."""
    incidents = list(incidents)
    buses = {b.name: b for b in Bus.objects.filter(name__in={i.bus_id for i in incidents})}
    return [i.to_dict(buses.get(i.bus_id)) for i in incidents]


def today_stats(open_now):
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    today = Incident.objects.filter(created_at__gte=start)
    responses = [(i.on_way_at - i.assigned_at).total_seconds()
                 for i in today.filter(on_way_at__isnull=False, assigned_at__isnull=False)]
    return {
        "today": today.count(),
        "open": open_now,
        "resolved": today.filter(status=Incident.Status.RESOLVED).count(),
        "avg_response_seconds": round(sum(responses) / len(responses)) if responses else None,
    }


@checker_required
@require_GET
def nearby_officers(request, pk):
    """All officers, nearest to where the bus is NOW first. On-duty, free officers come first."""
    incident = get_object_or_404(Incident, pk=pk)
    live = with_live_bus([incident])[0]
    bus_lat, bus_lon = live["bus_latitude"], live["bus_longitude"]
    fresh = timezone.now() - LOCATION_FRESH_FOR
    busy_ids = set(Incident.objects.filter(status__in=[Incident.Status.ASSIGNED, Incident.Status.ON_WAY])
                   .exclude(pk=incident.pk).values_list("assigned_officer_id", flat=True))
    rows = []
    for p in Profile.objects.filter(role=Profile.Role.OFFICER).select_related("user"):
        has_location = p.latitude is not None and p.longitude is not None
        online = p.is_online(fresh)
        km = None
        if has_location and bus_lat is not None and bus_lon is not None:
            km = round(distance_km(bus_lat, bus_lon, p.latitude, p.longitude), 1)
        rows.append({
            "id": p.user_id,
            "name": p.display_name,
            "phone": p.phone,
            "online": online,
            "current": p.user_id == incident.assigned_officer_id,
            "busy": p.user_id in busy_ids,
            "distance_km": km,
            "last_seen": (timezone.localtime(p.location_updated_at).strftime("%H:%M")
                          if p.location_updated_at else None),
        })
    rows.sort(key=lambda r: (not r["online"], r["busy"] or r["current"], r["distance_km"] is None,
                             r["distance_km"] or 0))
    return JsonResponse({"officers": rows})


@checker_required
@require_POST
def dismiss(request, pk):
    incident = get_object_or_404(Incident, pk=pk)
    if incident.status not in (Incident.Status.NEW, Incident.Status.ERROR):
        return JsonResponse({"error": "This incident has already been handled."}, status=409)
    incident.status = Incident.Status.DISMISSED
    incident.checked_by = request.user
    incident.checker_note = request.POST.get("note", "")[:2000]
    incident.closed_at = timezone.now()
    incident.save()
    return JsonResponse({"ok": True, "incident": incident.to_dict()})


@checker_required
@require_POST
def assign(request, pk):
    """Send the incident to an officer. Also used to re-send it to another officer
    when the first one hasn't replied."""
    incident = get_object_or_404(Incident, pk=pk)
    if incident.status not in (Incident.Status.NEW, Incident.Status.ERROR, Incident.Status.ASSIGNED):
        return JsonResponse({"error": "This incident has already been handled."}, status=409)
    officer = Profile.objects.filter(role=Profile.Role.OFFICER,
                                     user_id=request.POST.get("officer_id")).select_related("user").first()
    if not officer:
        return JsonResponse({"error": "Officer not found."}, status=400)
    if officer.user_id == incident.assigned_officer_id:
        return JsonResponse({"error": "That officer already has this incident."}, status=409)
    incident.status = Incident.Status.ASSIGNED
    incident.checked_by = request.user
    note = request.POST.get("note", "").strip()
    if note:
        incident.checker_note = note[:2000]
    incident.assigned_officer = officer.user
    incident.assigned_at = timezone.now()
    incident.save()
    return JsonResponse({"ok": True, "incident": incident.to_dict()})


# ---------- 3. Field officer (phone) ----------

@officer_required
@ensure_csrf_cookie
def officer_screen(request):
    return render(request, "alerts/officer.html", {"profile": request.user.profile})


@officer_required
@require_POST
def officer_location(request):
    """The officer's phone sends its GPS position (and on/off duty) here."""
    profile = request.user.profile
    if "on_duty" in request.POST:
        profile.on_duty = request.POST["on_duty"] == "true"
    lat, lon = as_float(request.POST.get("latitude")), as_float(request.POST.get("longitude"))
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
        profile.latitude, profile.longitude = lat, lon
        profile.location_updated_at = timezone.now()
    profile.save()
    return JsonResponse({"ok": True, "on_duty": profile.on_duty})


@officer_required
@require_GET
def officer_feed(request):
    jobs = Incident.objects.filter(assigned_officer=request.user,
                                   status__in=[Incident.Status.ASSIGNED, Incident.Status.ON_WAY])
    return JsonResponse({
        "on_duty": request.user.profile.on_duty,
        "incidents": with_live_bus(jobs),
    })


def _officer_update(request, pk, allowed_from, new_status):
    incident = get_object_or_404(Incident, pk=pk, assigned_officer=request.user)
    if incident.status not in allowed_from:
        return JsonResponse({"error": "This incident can't be changed now."}, status=409)
    incident.status = new_status
    now = timezone.now()
    if new_status == Incident.Status.ON_WAY:
        incident.on_way_at = now
    else:
        incident.closed_at = now
        incident.officer_note = request.POST.get("note", "")[:2000]
        answer = request.POST.get("real_fight")
        if answer in ("yes", "no"):
            incident.real_fight = answer == "yes"
    incident.save()
    return JsonResponse({"ok": True, "incident": incident.to_dict()})


@officer_required
@require_POST
def officer_on_way(request, pk):
    return _officer_update(request, pk, [Incident.Status.ASSIGNED], Incident.Status.ON_WAY)


@officer_required
@require_POST
def officer_resolve(request, pk):
    return _officer_update(request, pk, [Incident.Status.ASSIGNED, Incident.Status.ON_WAY],
                           Incident.Status.RESOLVED)
