"""
System manager pages (/manage/): accounts, buses & areas, incident history, AI improvement.

Only users with the "System manager" role (or a Django superuser) can open these.
"""
import csv
from datetime import datetime, time, timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import gps_feed
from .models import Bus, GpsFeed, Incident, Profile, new_device_key
from .views import role_required

User = get_user_model()
manager_required = role_required(Profile.Role.MANAGER)


# ---------- Forms ----------

class AccountForm(forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    role = forms.ChoiceField(choices=Profile.Role.choices)
    phone = forms.CharField(max_length=30, required=False)
    password = forms.CharField(widget=forms.PasswordInput, required=False,
                               help_text="Leave empty to keep the current password.")

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user is None:
            self.fields["password"].required = True
            self.fields["password"].help_text = "At least 8 characters."

    def clean_username(self):
        name = self.cleaned_data["username"].strip()
        clash = User.objects.filter(username__iexact=name)
        if self.user:
            clash = clash.exclude(pk=self.user.pk)
        if clash.exists():
            raise forms.ValidationError("That username is already taken.")
        return name

    def clean_password(self):
        pw = self.cleaned_data.get("password")
        if pw:
            validate_password(pw, self.user)
        return pw

    def save(self):
        u = self.user or User()
        d = self.cleaned_data
        u.username, u.first_name, u.last_name = d["username"], d["first_name"], d["last_name"]
        if d["password"]:
            u.set_password(d["password"])
        u.save()
        profile, _ = Profile.objects.get_or_create(user=u)
        profile.role, profile.phone = d["role"], d["phone"]
        if profile.role != Profile.Role.OFFICER:
            profile.on_duty = False
        profile.save()
        return u


class BusForm(forms.ModelForm):
    """A bus or a station/area with a CCTV camera (or a phone used as the camera).

    GPS: a phone camera always uses the phone's own GPS. A CCTV camera uses the bus's
    GPS tracker (through the fleet GPS feed), or a fixed location (no GPS, e.g. a station)."""
    gps_source = forms.ChoiceField(
        label="Where does the position come from?", required=False, widget=forms.RadioSelect,
        choices=[(Bus.GpsSource.FEED, Bus.GpsSource.FEED.label), (Bus.GpsSource.FIXED, Bus.GpsSource.FIXED.label)])

    class Meta:
        model = Bus
        fields = ["kind", "name", "enabled", "notes", "camera_type", "camera_address",
                  "gps_source", "vehicle_id", "fixed_latitude", "fixed_longitude"]
        labels = {
            "kind": "What is it?",
            "name": "Name",
            "enabled": "Active (allowed to connect)",
            "camera_type": "Which camera?",
            "camera_address": "CCTV stream address",
            "vehicle_id": "Bus GPS tracker ID",
            "fixed_latitude": "Latitude",
            "fixed_longitude": "Longitude",
        }
        widgets = {"kind": forms.RadioSelect, "camera_type": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.initial.setdefault("gps_source", Bus.GpsSource.FEED)
        elif self.instance.gps_source not in (Bus.GpsSource.FEED, Bus.GpsSource.FIXED):
            self.initial["gps_source"] = Bus.GpsSource.FEED

    def clean(self):
        d = super().clean()
        if d.get("camera_type") == Bus.CameraType.PHONE:
            d["gps_source"] = Bus.GpsSource.TRACKER        # the phone sends its own GPS
            return d
        if d.get("camera_type") == Bus.CameraType.CCTV and not d.get("camera_address", "").strip():
            self.add_error("camera_address", "Enter the CCTV camera's live stream address (rtsp://...).")
        if not d.get("gps_source"):
            self.add_error("gps_source", "Choose the bus GPS tracker or a fixed location.")
        if d.get("gps_source") == Bus.GpsSource.FIXED and (d.get("fixed_latitude") is None
                                                            or d.get("fixed_longitude") is None):
            self.add_error("fixed_latitude", "Enter a latitude and longitude, or click the map.")
        if d.get("gps_source") == Bus.GpsSource.FEED and not d.get("vehicle_id", "").strip():
            self.add_error("vehicle_id", "Enter the bus's GPS tracker ID (its Vehicle ID in the fleet GPS feed).")
        return d


class GpsFeedForm(forms.ModelForm):
    """The bus operator's tracking feed. Shown inside the bus form when GPS = fleet feed.
    There is one feed for the whole fleet, so changing it here changes it for every feed bus."""
    class Meta:
        model = GpsFeed
        fields = ["url", "header_name", "header_value"]
        labels = {
            "url": "Fleet GPS feed address",
            "header_name": "Access key header name (if the feed needs one)",
            "header_value": "Access key",
        }
        widgets = {"header_value": forms.PasswordInput(render_value=True)}

    def __init__(self, *args, required=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["url"].required = required


def style(form):
    """Add Bootstrap classes so the forms look right."""
    for f in form.fields.values():
        w = f.widget
        if isinstance(w, forms.CheckboxInput):
            w.attrs["class"] = "form-check-input"
        elif isinstance(w, forms.RadioSelect):
            w.attrs["class"] = "form-check-input"
        elif isinstance(w, forms.Select):
            w.attrs["class"] = "form-select"
        else:
            w.attrs["class"] = "form-control"
    return form


# ---------- Dashboard ----------

@manager_required
def dashboard(request):
    roles = dict(Profile.objects.values_list("role").annotate(n=Count("id")))
    buses = list(Bus.objects.all())
    return render(request, "manage/dashboard.html", {
        "managers": roles.get(Profile.Role.MANAGER, 0),
        "checkers": roles.get(Profile.Role.CHECKER, 0),
        "officers": roles.get(Profile.Role.OFFICER, 0),
        "buses": len(buses),
        "buses_online": sum(1 for b in buses if b.is_online()),
        "open_incidents": Incident.objects.filter(status__in=Incident.ACTIVE).count(),
    })


# ---------- Incident history ----------

HISTORY_SHOW = [
    ("", "All incidents"),
    ("open", "Still open"),
    ("resolved", "Resolved"),
    ("real_fight", "Resolved: real fight"),
    ("not_fight", "Resolved: not a fight"),
    ("dismissed", "Dismissed (false alarm)"),
    ("manual", "Manual alerts"),
]


def _parse_day(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def filtered_incidents(params):
    """Incidents matching the history page's search boxes. Returns (queryset, filters used)."""
    q = params.get("q", "").strip()
    show = params.get("show", "")
    day_from, day_to = _parse_day(params.get("from")), _parse_day(params.get("to"))
    incidents = Incident.objects.select_related("assigned_officer", "checked_by")
    if q:
        incidents = incidents.filter(Q(bus_id__icontains=q) | Q(checker_note__icontains=q) |
                                     Q(officer_note__icontains=q) | Q(assigned_officer__first_name__icontains=q) |
                                     Q(assigned_officer__last_name__icontains=q))
    filters = {
        "open": Q(status__in=Incident.ACTIVE),
        "resolved": Q(status=Incident.Status.RESOLVED),
        "real_fight": Q(status=Incident.Status.RESOLVED, real_fight=True),
        "not_fight": Q(status=Incident.Status.RESOLVED, real_fight=False),
        "dismissed": Q(status=Incident.Status.DISMISSED),
        "manual": Q(fight_score__isnull=True),
    }
    if show in filters:
        incidents = incidents.filter(filters[show])
    tz = timezone.get_current_timezone()
    if day_from:
        incidents = incidents.filter(created_at__gte=datetime.combine(day_from, time.min, tz))
    if day_to:
        incidents = incidents.filter(created_at__lt=datetime.combine(day_to + timedelta(days=1), time.min, tz))
    return incidents, {"q": q, "show": show, "from": params.get("from", ""), "to": params.get("to", "")}


def _minutes(a, b):
    return round((b - a).total_seconds() / 60, 1) if a and b else None


def _outcome(incident):
    """Short words for how it ended."""
    if incident.status == Incident.Status.RESOLVED:
        return {True: "Real fight", False: "Not a fight"}.get(incident.real_fight, "Resolved")
    return incident.get_status_display()


@manager_required
def incident_history(request):
    incidents, filters = filtered_incidents(request.GET)
    if request.GET.get("download") == "csv":
        return _history_csv(incidents)
    page = Paginator(incidents, 25).get_page(request.GET.get("page"))
    for i in page:
        i.response_minutes = _minutes(i.assigned_at, i.on_way_at)
        i.total_minutes = _minutes(i.created_at, i.closed_at)
        i.outcome = _outcome(i)
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "manage/history.html", {
        "page": page, "filters": filters, "show_choices": HISTORY_SHOW, "query": query.urlencode(),
        "total": incidents.count(),
    })


def _history_csv(incidents):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="busguard_incidents_{timezone.localdate():%Y%m%d}.csv"')
    writer = csv.writer(response)
    writer.writerow(["Incident", "Reported", "Bus or area", "Latitude", "Longitude", "AI fight score %",
                     "Status", "Outcome", "Checked by", "Control room note", "Officer", "Sent to officer",
                     "Officer on the way", "Closed", "Officer response (min)", "Total time (min)",
                     "Officer report"])

    def when(dt):
        return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M:%S") if dt else ""

    for i in incidents:
        writer.writerow([
            i.pk, when(i.created_at), i.bus_id, i.latitude or "", i.longitude or "",
            "" if i.fight_score is None else round(i.fight_score * 100), i.get_status_display(), _outcome(i),
            i.checked_by.get_full_name() or i.checked_by.username if i.checked_by else "", i.checker_note,
            i.assigned_officer.get_full_name() or i.assigned_officer.username if i.assigned_officer else "",
            when(i.assigned_at), when(i.on_way_at), when(i.closed_at),
            _minutes(i.assigned_at, i.on_way_at) or "", _minutes(i.created_at, i.closed_at) or "",
            i.officer_note,
        ])
    return response


@manager_required
def incident_detail(request, pk):
    incident = get_object_or_404(Incident.objects.select_related("assigned_officer", "checked_by"), pk=pk)
    incident.response_minutes = _minutes(incident.assigned_at, incident.on_way_at)
    incident.total_minutes = _minutes(incident.created_at, incident.closed_at)
    incident.outcome = _outcome(incident)
    return render(request, "manage/incident.html", {"i": incident, "back": request.GET.get("back", "")})


# ---------- AI improvement ----------

@manager_required
def ai_improvement(request):
    """Retrain the AI with incidents people have checked (live numbers come from /api/ai/status/)."""
    return render(request, "manage/ai.html")


# ---------- Accounts ----------

@manager_required
def account_list(request):
    users = User.objects.select_related("profile").order_by("username")
    role = request.GET.get("role", "")
    if role:
        users = users.filter(profile__role=role)
    return render(request, "manage/accounts.html", {"users": users, "role": role,
                                                    "roles": Profile.Role.choices})


@manager_required
def account_edit(request, pk=None):
    user = get_object_or_404(User, pk=pk) if pk else None
    if request.method == "POST":
        form = AccountForm(request.POST, user=user)
        if form.is_valid():
            if user == request.user and form.cleaned_data["role"] != Profile.Role.MANAGER \
                    and not request.user.is_superuser:
                form.add_error("role", "You can't remove your own manager role.")
            else:
                saved = form.save()
                messages.success(request, f"Account '{saved.username}' saved.")
                return redirect("alerts:manage_accounts")
    else:
        initial = {}
        if user:
            profile = getattr(user, "profile", None)
            initial = {"username": user.username, "first_name": user.first_name, "last_name": user.last_name,
                       "role": profile.role if profile else Profile.Role.OFFICER,
                       "phone": profile.phone if profile else ""}
        form = AccountForm(initial=initial, user=user)
    return render(request, "manage/account_form.html", {"form": style(form), "account": user})


@manager_required
def account_delete(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user == request.user:
        messages.error(request, "You can't delete your own account.")
        return redirect("alerts:manage_accounts")
    if request.method == "POST":
        name = user.username
        user.delete()
        messages.success(request, f"Account '{name}' deleted.")
        return redirect("alerts:manage_accounts")
    return render(request, "manage/confirm_delete.html", {
        "what": f"the account '{user.username}'",
        "details": "Incidents this person handled are kept, but their name is removed from them.",
        "back": reverse("alerts:manage_accounts"),
    })


# ---------- Buses ----------

@manager_required
def bus_list(request):
    return render(request, "manage/buses.html", {"buses": Bus.objects.all()})


@manager_required
def bus_edit(request, pk=None):
    bus = get_object_or_404(Bus, pk=pk) if pk else None
    feed = GpsFeed.get()
    form = BusForm(request.POST or None, instance=bus)
    uses_feed = (request.POST.get("gps_source") == Bus.GpsSource.FEED
                 and request.POST.get("camera_type") != Bus.CameraType.PHONE)
    feed_form = GpsFeedForm(request.POST or None, instance=feed, prefix="feed", required=uses_feed)
    if request.method == "POST" and form.is_valid() and (not uses_feed or feed_form.is_valid()):
        saved = form.save()
        if saved.gps_source == Bus.GpsSource.FIXED:
            saved.latitude, saved.longitude = saved.fixed_latitude, saved.fixed_longitude
            saved.save(update_fields=["latitude", "longitude"])
        if uses_feed:
            feed = feed_form.save(commit=False)
            feed.enabled = True
            feed.save()
            gps_feed.poll_once()          # get this bus's position straight away
        messages.success(request, f"{saved.name} saved.")
        return redirect("alerts:manage_bus_connect", pk=saved.pk)
    return render(request, "manage/bus_form.html", {
        "form": style(form), "feed_form": style(feed_form), "bus": bus,
    })


@manager_required
def bus_connect(request, pk):
    """How to connect this bus's camera and GPS."""
    bus = get_object_or_404(Bus, pk=pk)
    server_url = request.build_absolute_uri("/").rstrip("/")
    command = f"python camera.py --server {server_url} --key {bus.device_key}"
    return render(request, "manage/bus_connect.html", {
        "bus": bus,
        "feed": GpsFeed.get(),
        "phone_url": request.build_absolute_uri(reverse("alerts:phone_camera", args=[bus.device_key])),
        "command": command,
    })


@manager_required
@require_POST
def bus_test_gps(request, pk):
    """'Test GPS' button: read the fleet feed now and say whether this bus was found."""
    bus = get_object_or_404(Bus, pk=pk)
    if bus.gps_source != Bus.GpsSource.FEED:
        if bus.latitude is None:
            messages.warning(request, f"{bus.name} has no position yet.")
        else:
            messages.success(request, f"{bus.name} is at {bus.latitude:.5f}, {bus.longitude:.5f}.")
        return redirect("alerts:manage_bus_connect", pk=bus.pk)
    try:
        vehicles = gps_feed.fetch_vehicles(GpsFeed.get())
    except Exception as e:
        messages.error(request, f"Could not read the fleet GPS feed: {e}")
        return redirect("alerts:manage_bus_connect", pk=bus.pk)
    match = gps_feed.match_vehicle(bus, vehicles)
    if match:
        gps_feed.poll_once()
        messages.success(request, f"GPS connected: vehicle {bus.vehicle_id} found in the feed at "
                                  f"{match['latitude']:.5f}, {match['longitude']:.5f}.")
    else:
        ids = ", ".join(v["id"] for v in vehicles[:15]) or "none"
        messages.error(request, f"Vehicle {bus.vehicle_id} is not in the feed. Vehicle IDs in the feed: {ids}")
    return redirect("alerts:manage_bus_connect", pk=bus.pk)


@manager_required
@require_POST
def bus_new_key(request, pk):
    bus = get_object_or_404(Bus, pk=pk)
    bus.device_key = new_device_key()
    bus.save(update_fields=["device_key"])
    messages.warning(request, f"New key made for {bus.name}. The old key no longer works.")
    return redirect("alerts:manage_bus_connect", pk=bus.pk)


@manager_required
def bus_delete(request, pk):
    bus = get_object_or_404(Bus, pk=pk)
    if request.method == "POST":
        name = bus.name
        bus.delete()
        messages.success(request, f"{name} deleted.")
        return redirect("alerts:manage_buses")
    return render(request, "manage/confirm_delete.html", {
        "what": bus.name,
        "details": "Its camera won't be able to connect any more. Past incidents from this bus are kept.",
        "back": reverse("alerts:manage_buses"),
    })
