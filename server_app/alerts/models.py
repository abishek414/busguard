import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class Profile(models.Model):
    """Extra info for each user: their job in BusGuard."""

    class Role(models.TextChoices):
        MANAGER = "manager", "System manager"
        CHECKER = "checker", "Control room checker"
        OFFICER = "officer", "Field officer"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="profile")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OFFICER)
    phone = models.CharField(max_length=30, blank=True)

    # Officers only: where they are now (sent by their phone)
    on_duty = models.BooleanField(default=False)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    location_updated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.display_name} ({self.get_role_display()})"

    def is_online(self, fresh_after):
        """On duty and has sent a location recently."""
        if not self.on_duty or self.latitude is None:
            return False
        return self.location_updated_at is not None and self.location_updated_at >= fresh_after

    @property
    def display_name(self):
        return self.user.get_full_name() or self.user.get_username()


def new_device_key():
    return secrets.token_urlsafe(12)


class Bus(models.Model):
    """A bus, or a station/area, with a camera. Added by the system manager."""

    ONLINE_FOR_SECONDS = 20

    class Kind(models.TextChoices):
        BUS = "bus", "Bus"
        AREA = "area", "Station or area"

    class CameraType(models.TextChoices):
        CCTV = "cctv", "CCTV camera (live stream address)"
        PHONE = "phone", "Phone used as the camera"

    class GpsSource(models.TextChoices):
        FEED = "feed", "Bus GPS tracker"
        TRACKER = "tracker", "Phone's GPS"
        FIXED = "fixed", "Fixed location (no GPS needed, e.g. a station)"

    name = models.CharField(max_length=100, unique=True)
    # Secret key the camera computer (or phone camera page) uses to connect
    device_key = models.CharField(max_length=40, unique=True, default=new_device_key)
    enabled = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.BUS)
    camera_type = models.CharField(max_length=20, choices=CameraType.choices, default=CameraType.CCTV)
    camera_address = models.CharField(
        max_length=300, blank=True,
        help_text="The CCTV camera's live stream address, e.g. rtsp://192.168.1.10:554/stream1")
    gps_source = models.CharField(max_length=20, choices=GpsSource.choices, default=GpsSource.FEED)
    fixed_latitude = models.FloatField(null=True, blank=True)
    fixed_longitude = models.FloatField(null=True, blank=True)
    # The bus's ID in the fleet tracking system (GTFS-Realtime vehicle id or label)
    vehicle_id = models.CharField(max_length=60, blank=True)
    location_updated_at = models.DateTimeField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_score = models.FloatField(null=True, blank=True)
    last_note = models.CharField(max_length=255, blank=True)
    frame_path = models.CharField(max_length=255, blank=True)  # latest photo, inside MEDIA_ROOT

    # Used by Azure to decide when to ask the bus for a clip: the scores of the last few
    # photos (newest last, e.g. "0.05,0.91,0.40,0.88"); an alert needs FIGHT_HITS of the
    # last FIGHT_WINDOW photos to look like a fight
    recent_scores = models.CharField(max_length=200, blank=True)
    alert_score = models.FloatField(null=True, blank=True)
    last_alert_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "buses"

    def __str__(self):
        return self.name

    def add_score(self, score, window):
        """Remember this photo's score; keep only the last `window` scores."""
        scores = [float(x) for x in self.recent_scores.split(",") if x] + [round(score, 3)]
        scores = scores[-window:]
        self.recent_scores = ",".join(f"{s:g}" for s in scores)
        return scores

    def is_online(self):
        return (self.last_seen is not None and
                (timezone.now() - self.last_seen).total_seconds() < self.ONLINE_FOR_SECONDS)

    def set_location_from_device(self, lat, lon):
        """Position sent with a photo. Only a phone camera sends its GPS; a bus GPS tracker
        is read from the fleet GPS feed, and a station has a fixed location."""
        if self.gps_source == self.GpsSource.FIXED:
            self.latitude, self.longitude = self.fixed_latitude, self.fixed_longitude
        elif self.gps_source == self.GpsSource.TRACKER:
            if lat is not None and lon is not None:
                self.latitude, self.longitude = lat, lon
                self.location_updated_at = timezone.now()

    def config(self):
        """What the bus's camera computer needs to know (sent to it when it connects)."""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "gps_source": self.gps_source,
            "camera_type": self.camera_type,
            "camera_address": self.camera_address,
        }

    def to_dict(self, show_frame=True):
        frame_url = ""
        if show_frame and self.frame_path and self.last_seen:
            frame_url = f"{settings.MEDIA_URL}{self.frame_path}?t={int(self.last_seen.timestamp())}"
        return {
            "name": self.name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "online": self.is_online(),
            "seconds_ago": (int((timezone.now() - self.last_seen).total_seconds())
                            if self.last_seen else None),
            "score": None if self.last_score is None else round(self.last_score * 100),
            "fight": self.last_score is not None and self.last_score >= settings.FIGHT_THRESHOLD,
            "frame_url": frame_url,
        }


class GpsFeed(models.Model):
    """Settings for the fleet GPS feed (only one row). Bus operators' tracking systems
    publish every bus's position in the standard GTFS-Realtime format."""

    url = models.URLField("Feed address (URL)", max_length=500, blank=True)
    header_name = models.CharField("Access key header name", max_length=60, blank=True,
                                   help_text='Only if the feed needs a key, e.g. "x-api-key" or "Authorization".')
    header_value = models.CharField("Access key", max_length=300, blank=True)
    enabled = models.BooleanField("Use this feed", default=False)
    poll_seconds = models.PositiveIntegerField("Check every (seconds)", default=10)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_ok = models.BooleanField(default=False)
    last_message = models.CharField(max_length=255, blank=True)
    last_vehicle_count = models.PositiveIntegerField(default=0)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return self.url or "(no feed)"


class TrainingRun(models.Model):
    """One 'Retrain AI' run on the server: what it learned from and whether it was better."""

    class Status(models.TextChoices):
        RUNNING = "running", "Training"
        ACTIVATED = "activated", "New model activated"
        KEPT_OLD = "kept_old", "Kept the old model (new one was not better)"
        FAILED = "failed", "Failed"

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    progress = models.CharField(max_length=255, blank=True)
    new_fight = models.PositiveIntegerField(default=0)    # bus incidents labelled "fight"
    new_normal = models.PositiveIntegerField(default=0)   # bus incidents labelled "normal"
    old_accuracy = models.FloatField(null=True, blank=True)
    new_accuracy = models.FloatField(null=True, blank=True)
    version = models.PositiveIntegerField(null=True, blank=True)  # model version if activated
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def to_dict(self):
        def pct(x):
            return None if x is None else round(x * 100, 1)
        return {
            "id": self.pk,
            "started": timezone.localtime(self.started_at).strftime("%d %b %H:%M"),
            "status": self.status,
            "status_display": self.get_status_display(),
            "progress": self.progress,
            "new_fight": self.new_fight,
            "new_normal": self.new_normal,
            "old_accuracy": pct(self.old_accuracy),
            "new_accuracy": pct(self.new_accuracy),
            "version": self.version,
            "error": self.error[-300:],
        }


class Incident(models.Model):
    class Status(models.TextChoices):
        ANALYSING = "analysing", "Preparing clip"
        NEW = "new", "New alert - check now"
        NORMAL = "normal", "Cleared"
        DISMISSED = "dismissed", "Dismissed"
        ASSIGNED = "assigned", "Sent to officer"
        ON_WAY = "on_way", "Officer on the way"
        RESOLVED = "resolved", "Resolved"
        ERROR = "error", "Clip problem - check manually"

    # Statuses that still need someone to do something
    ACTIVE = [Status.ANALYSING, Status.NEW, Status.ASSIGNED, Status.ON_WAY, Status.ERROR]

    bus_id = models.CharField(max_length=100)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    video = models.FileField(upload_to="clips/%Y/%m/%d/")
    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ANALYSING)
    fight_score = models.FloatField(null=True, blank=True)
    ai_note = models.CharField(max_length=255, blank=True)

    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="checked_incidents")
    checker_note = models.TextField(blank=True)
    assigned_officer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name="assigned_incidents")
    assigned_at = models.DateTimeField(null=True, blank=True)
    on_way_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    officer_note = models.TextField(blank=True)
    # The officer's answer when resolving: was it really a fight? (used to teach the AI)
    real_fight = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.pk} {self.bus_id} ({self.get_status_display()})"

    @property
    def map_url(self):
        if self.latitude is None or self.longitude is None:
            return ""
        return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"

    @property
    def directions_url(self):
        if self.latitude is None or self.longitude is None:
            return ""
        return f"https://www.google.com/maps/dir/?api=1&destination={self.latitude},{self.longitude}"

    @property
    def training_label(self):
        """What a human decided this clip shows - the 'correct answer' for retraining.
        Dismissed by the checker = normal (a false alarm). Resolved = the officer's answer."""
        if self.status == self.Status.DISMISSED:
            return "normal"
        if self.status == self.Status.RESOLVED and self.real_fight is not None:
            return "fight" if self.real_fight else "normal"
        return None

    @property
    def no_response(self):
        """Sent to an officer, but they haven't tapped 'On my way' in time."""
        return (self.status == self.Status.ASSIGNED and self.assigned_at is not None and
                (timezone.now() - self.assigned_at).total_seconds() > settings.OFFICER_REPLY_SECONDS)

    def to_dict(self, live_bus=None):
        """Everything the control room / officer screens need, as JSON-friendly data.

        live_bus: the Bus this incident came from, so screens can follow where the bus is NOW
        (it keeps driving after the incident is reported)."""
        officer = getattr(self.assigned_officer, "profile", None) if self.assigned_officer else None
        local = timezone.localtime(self.created_at)

        def hms(dt):
            return timezone.localtime(dt).strftime("%H:%M:%S") if dt else None

        def mins(a, b):
            return round((b - a).total_seconds() / 60, 1) if a and b else None

        bus_lat = live_bus.latitude if live_bus and live_bus.is_online() else self.latitude
        bus_lon = live_bus.longitude if live_bus and live_bus.is_online() else self.longitude
        directions = (f"https://www.google.com/maps/dir/?api=1&destination={bus_lat},{bus_lon}"
                      if bus_lat is not None else "")
        return {
            "id": self.pk,
            "bus_id": self.bus_id,
            "latitude": self.latitude,          # where it was reported
            "longitude": self.longitude,
            "bus_latitude": bus_lat,            # where the bus is now
            "bus_longitude": bus_lon,
            "bus_live": bool(live_bus and live_bus.is_online()),
            "video_url": self.video.url if self.video else "",
            "time": local.strftime("%H:%M:%S"),
            "date": local.strftime("%a %d %b"),
            "status": self.status,
            "status_display": self.get_status_display(),
            "score": None if self.fight_score is None else round(self.fight_score * 100),
            "ai_note": self.ai_note,
            "map_url": self.map_url,
            "directions_url": directions,
            "checker_note": self.checker_note,
            "officer_note": self.officer_note,
            "officer_name": officer.display_name if officer else None,
            "officer_phone": officer.phone if officer else None,
            "officer_id": self.assigned_officer_id,
            "real_fight": self.real_fight,
            "training_label": self.training_label,
            "no_response": self.no_response,
            "waiting_seconds": (int((timezone.now() - self.assigned_at).total_seconds())
                                if self.status == self.Status.ASSIGNED and self.assigned_at else None),
            "timeline": {
                "reported": hms(self.created_at),
                "sent": hms(self.assigned_at),
                "on_way": hms(self.on_way_at),
                "closed": hms(self.closed_at),
                "response_minutes": mins(self.assigned_at, self.on_way_at),
                "total_minutes": mins(self.created_at, self.closed_at),
            },
        }
