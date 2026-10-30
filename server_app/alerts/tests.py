"""
Automatic tests for the whole flow. The AI is replaced by fake scores.
"""
import shutil
import tempfile
from unittest import mock

import cv2
import numpy as np
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from django.contrib.auth.models import User
from django.utils import timezone

from .models import Bus, Incident, Profile

TEMP_MEDIA = tempfile.mkdtemp()


class RunNow:
    """Replaces threading.Thread so background work runs immediately in tests."""
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def photo():
    ok, buf = cv2.imencode(".jpg", np.zeros((180, 320, 3), np.uint8))
    return SimpleUploadedFile("frame.jpg", buf.tobytes(), content_type="image/jpeg")


def setup_accounts():
    call_command("setup_accounts", stdout=open(tempfile.mktemp(), "w"))


def add_officer(username, name, lat, lon):
    """Another field officer, on duty at a place in Darwin."""
    first, last = name.split()
    user = User.objects.create_user(username, password="busguard123", first_name=first, last_name=last)
    Profile.objects.create(user=user, role=Profile.Role.OFFICER, on_duty=True, latitude=lat, longitude=lon,
                           location_updated_at=timezone.now())
    return user


@override_settings(MEDIA_ROOT=TEMP_MEDIA, FIGHT_THRESHOLD=0.6, FIGHT_HITS=3, FIGHT_WINDOW=6, ALERT_COOLDOWN=60)
class FlowTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        setup_accounts()
        # The bus: a phone used as the camera, which also sends its GPS
        self.bus = Bus.objects.create(name="Bus 4", camera_type=Bus.CameraType.PHONE,
                                      gps_source=Bus.GpsSource.TRACKER)
        self.key = {"HTTP_X_BUS_KEY": self.bus.device_key}
        add_officer("officer2", "Priya Singh", -12.4634, 130.8456)   # Darwin CBD
        add_officer("officer3", "Jack Nguyen", -12.3800, 130.8700)   # near Casuarina

    def send_photo(self, score):
        with mock.patch("alerts.views.ai.score_frame", return_value=(score, "test")):
            return self.client.post("/api/frame/", {"latitude": "-12.376", "longitude": "130.88",
                                                    "image": photo()}, **self.key)

    def send_clip(self, manual=False):
        video = SimpleUploadedFile("clip.mp4", b"fake video", content_type="video/mp4")
        data = {"latitude": "-12.376", "longitude": "130.88", "video": video}
        if manual:
            data["manual"] = "1"
        with mock.patch("alerts.views.threading.Thread", RunNow), \
             mock.patch("alerts.views.ai.to_browser_mp4", side_effect=lambda p: p):
            return self.client.post("/api/upload/", data, **self.key)

    def test_only_known_bus_keys(self):
        for headers in ({}, {"HTTP_X_BUS_KEY": "wrong"}, {"HTTP_X_UPLOAD_TOKEN": "change-me"}):
            self.assertEqual(self.client.post("/api/frame/", {"image": photo()}, **headers).status_code, 403)
            self.assertEqual(self.client.post("/api/upload/", {}, **headers).status_code, 403)
        self.assertFalse(Bus.objects.exclude(pk=self.bus.pk).exists())   # no bus added automatically

    def test_normal_photos_never_ask_for_a_clip(self):
        for _ in range(10):
            r = self.send_photo(0.1).json()
            self.assertFalse(r["record"])
        bus = Bus.objects.get(name="Bus 4")
        self.assertTrue(bus.is_online())
        self.assertEqual(round(bus.latitude, 3), -12.376)

    def test_one_odd_photo_is_ignored(self):
        self.send_photo(0.9)
        self.send_photo(0.1)
        self.assertFalse(self.send_photo(0.9).json()["record"])

    def test_fight_with_blocked_photos_still_alerts(self):
        # 3 of the last 6: low photos in the middle (someone blocking the camera) don't reset it
        replies = [self.send_photo(s).json() for s in (0.85, 0.40, 0.90, 0.30)]
        self.assertFalse(any(r["record"] for r in replies))
        self.assertEqual((replies[-1]["hits"], replies[-1]["window"]), (2, 6))
        self.assertTrue(self.send_photo(0.88).json()["record"])
        self.assertAlmostEqual(Bus.objects.get().alert_score, 0.90)

    def test_old_fight_photos_drop_out_of_the_window(self):
        # Two fight-like photos, then 5 normal ones: they're more than 6 photos ago now
        for s in (0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1):
            self.assertFalse(self.send_photo(s).json()["record"])
        self.assertFalse(self.send_photo(0.9).json()["record"])     # only 1 of the last 6

    def test_streak_asks_for_clip_then_cooldown(self):
        self.assertFalse(self.send_photo(0.8).json()["record"])
        self.assertFalse(self.send_photo(0.9).json()["record"])
        self.assertTrue(self.send_photo(0.85).json()["record"])
        # Within the cooldown, another streak does not ask again
        for _ in range(3):
            self.assertFalse(self.send_photo(0.9).json()["record"])

    def test_one_open_incident_per_bus(self):
        from datetime import timedelta
        from django.utils import timezone
        for s in (0.9, 0.9, 0.9):
            r = self.send_photo(s).json()
        self.assertTrue(r["record"])
        self.send_clip()
        incident = Incident.objects.get()
        # Cooldown over, fight still on camera: no second alert while the first is open
        Bus.objects.update(last_alert_at=timezone.now() - timedelta(minutes=5))
        for _ in range(4):
            self.assertFalse(self.send_photo(0.9).json()["record"])
        # Once it's closed, a new fight can raise a new alert
        Incident.objects.filter(pk=incident.pk).update(status=Incident.Status.RESOLVED)
        results = [self.send_photo(0.9).json()["record"] for _ in range(3)]
        self.assertTrue(any(results))

    def test_full_flow(self):
        for s in (0.8, 0.95, 0.85):
            r = self.send_photo(s).json()
        self.assertTrue(r["record"])
        self.assertEqual(self.send_clip().status_code, 201)
        incident = Incident.objects.get()
        self.assertEqual(incident.status, Incident.Status.NEW)
        self.assertAlmostEqual(incident.fight_score, 0.95)

        # Control room sees the incident, the bus and the officers
        self.client.login(username="control", password="busguard123")
        feed = self.client.get("/api/control/feed/").json()
        self.assertEqual(feed["incidents"][0]["id"], incident.pk)
        self.assertIn("Bus 4", [b["name"] for b in feed["buses"]])
        self.assertEqual(len(feed["officers"]), 3)
        officers = self.client.get(f"/api/incident/{incident.pk}/officers/").json()["officers"]
        self.assertEqual(officers[0]["name"], "Jack Nguyen")  # closest officer on duty

        # Officer1 comes on duty right next to the bus -> now nearest
        self.client.login(username="officer1", password="busguard123")
        self.client.post("/api/officer/location/", {"on_duty": "true", "latitude": "-12.377", "longitude": "130.881"})
        self.client.login(username="control", password="busguard123")
        officers = self.client.get(f"/api/incident/{incident.pk}/officers/").json()["officers"]
        self.assertEqual(officers[0]["name"], "Sam Taylor")
        r = self.client.post(f"/api/incident/{incident.pk}/assign/", {"officer_id": officers[0]["id"],
                                                                     "note": "back seats"})
        self.assertEqual(r.status_code, 200)

        # Officer sees it and responds
        self.client.login(username="officer1", password="busguard123")
        jobs = self.client.get("/api/officer/feed/").json()["incidents"]
        self.assertEqual(jobs[0]["checker_note"], "back seats")
        self.client.post(f"/api/incident/{incident.pk}/on-way/")
        self.client.post(f"/api/incident/{incident.pk}/resolve/", {"note": "Separated, no injuries"})
        incident.refresh_from_db()
        self.assertEqual(incident.status, Incident.Status.RESOLVED)

    def test_no_reply_then_reassign(self):
        from datetime import timedelta
        from django.utils import timezone
        self.send_clip(manual=True)
        incident = Incident.objects.get()
        self.client.login(username="control", password="busguard123")
        officers = self.client.get(f"/api/incident/{incident.pk}/officers/").json()["officers"]
        first, second = officers[0]["id"], officers[1]["id"]
        self.client.post(f"/api/incident/{incident.pk}/assign/", {"officer_id": first})
        # Pretend 2 minutes pass with no reply
        Incident.objects.filter(pk=incident.pk).update(assigned_at=timezone.now() - timedelta(minutes=2))
        feed = self.client.get("/api/control/feed/").json()
        self.assertTrue(feed["incidents"][0]["no_response"])
        # Same officer again is refused, a different one is accepted
        self.assertEqual(self.client.post(f"/api/incident/{incident.pk}/assign/",
                                          {"officer_id": first}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/incident/{incident.pk}/assign/",
                                          {"officer_id": second}).status_code, 200)
        feed = self.client.get("/api/control/feed/").json()
        self.assertFalse(feed["incidents"][0]["no_response"])
        self.assertEqual(feed["stats"]["open"], 1)

    def test_labels_for_learning(self):
        from .training import learning_summary
        # One dismissed (= normal), one resolved as a real fight, one still open (no label)
        for _ in range(3):
            self.send_clip(manual=True)
        a, b, c = Incident.objects.order_by("pk")
        self.client.login(username="control", password="busguard123")
        self.client.post(f"/api/incident/{a.pk}/dismiss/")
        officer_id = self.client.get(f"/api/incident/{b.pk}/officers/").json()["officers"][0]["id"]
        self.client.post(f"/api/incident/{b.pk}/assign/", {"officer_id": officer_id})
        assigned = Incident.objects.get(pk=b.pk).assigned_officer
        self.client.force_login(assigned)
        self.client.post(f"/api/incident/{b.pk}/resolve/", {"real_fight": "yes"})
        a.refresh_from_db(); b.refresh_from_db(); c.refresh_from_db()
        self.assertEqual(a.training_label, "normal")
        self.assertEqual(b.training_label, "fight")
        self.assertIsNone(c.training_label)
        s = learning_summary()
        self.assertEqual((s["fight"], s["normal"], s["new_since_last"]), (1, 1, 2))

    def test_ai_improvement_page_and_retrain_button(self):
        # Only the system manager has the AI improvement page and the Retrain button
        for user in ("control", "officer1"):
            self.client.login(username=user, password="busguard123")
            self.assertEqual(self.client.get("/manage/ai/").status_code, 403)
            self.assertEqual(self.client.get("/api/ai/status/").status_code, 403)
            self.assertEqual(self.client.post("/api/ai/retrain/").status_code, 403)
        # The control room no longer shows the AI learning card
        self.client.login(username="control", password="busguard123")
        self.assertNotContains(self.client.get("/control/"), "AI learning")
        self.assertNotIn("learning", self.client.get("/api/control/feed/").json())

        self.client.login(username="manager", password="busguard123")
        self.assertContains(self.client.get("/manage/"), "AI improvement")      # in the top bar
        self.assertContains(self.client.get("/manage/ai/"), "Retrain")
        self.send_clip(manual=True)
        self.client.post(f"/api/incident/{Incident.objects.get().pk}/dismiss/")
        status = self.client.get("/api/ai/status/").json()
        self.assertEqual((status["fight"], status["normal"]), (0, 1))
        with mock.patch("alerts.views.training.base_ready", return_value=False):
            self.assertEqual(self.client.post("/api/ai/retrain/").status_code, 400)
        with mock.patch("alerts.views.training.base_ready", return_value=True), \
             mock.patch("alerts.views.ai.model_ready", return_value=True), \
             mock.patch("subprocess.Popen") as popen, \
             override_settings(TRAINING_DIR=__import__("pathlib").Path(TEMP_MEDIA)):
            self.assertEqual(self.client.post("/api/ai/retrain/").status_code, 200)
            self.assertIn("retrain_model", popen.call_args[0][0])
            self.assertTrue(self.client.get("/api/ai/status/").json()["running"])
            # A second click while it's running is refused
            self.assertEqual(self.client.post("/api/ai/retrain/").status_code, 409)

    def test_closed_list_shows_last_24_hours(self):
        from datetime import timedelta
        for _ in range(2):
            self.send_clip(manual=True)
        old, new = Incident.objects.order_by("pk")
        Incident.objects.filter(pk=old.pk).update(status=Incident.Status.DISMISSED,
                                                  closed_at=timezone.now() - timedelta(hours=30))
        Incident.objects.filter(pk=new.pk).update(status=Incident.Status.DISMISSED,
                                                  closed_at=timezone.now() - timedelta(hours=2))
        self.client.login(username="control", password="busguard123")
        recent = [i["id"] for i in self.client.get("/api/control/feed/").json()["recent"]]
        self.assertEqual(recent, [new.pk])

    def test_incident_history(self):
        from datetime import timedelta
        for _ in range(3):
            self.send_clip(manual=True)
        a, b, c = Incident.objects.order_by("pk")
        officer = User.objects.get(username="officer1")
        Incident.objects.filter(pk=a.pk).update(status=Incident.Status.RESOLVED, real_fight=True,
                                                assigned_officer=officer, officer_note="Separated them",
                                                closed_at=timezone.now())
        Incident.objects.filter(pk=b.pk).update(status=Incident.Status.DISMISSED, checker_note="kids playing",
                                                closed_at=timezone.now(),
                                                created_at=timezone.now() - timedelta(days=10))
        # Only the manager can see it
        for user in ("control", "officer1"):
            self.client.login(username=user, password="busguard123")
            self.assertEqual(self.client.get("/manage/history/").status_code, 403)
        self.client.login(username="manager", password="busguard123")
        page = self.client.get("/manage/history/")
        self.assertContains(page, "Incident history")
        self.assertContains(page, "3 incidents found")
        self.assertEqual(len(self.client.get("/manage/history/?show=real_fight").context["page"]), 1)
        self.assertEqual(len(self.client.get("/manage/history/?show=open").context["page"]), 1)
        self.assertEqual(len(self.client.get("/manage/history/?q=kids").context["page"]), 1)
        self.assertEqual(len(self.client.get("/manage/history/?q=sam").context["page"]), 1)   # officer's name
        week_ago = (timezone.localdate() - timedelta(days=7)).isoformat()
        self.assertEqual(len(self.client.get(f"/manage/history/?from={week_ago}").context["page"]), 2)
        # Detail page with the clip, notes and outcome
        detail = self.client.get(f"/manage/history/{a.pk}/")
        self.assertContains(detail, "Separated them")
        self.assertContains(detail, "Real fight")
        self.assertContains(detail, "<video")
        # Spreadsheet download, with the same filters
        csv_text = self.client.get("/manage/history/?show=dismissed&download=csv").content.decode()
        self.assertEqual(len(csv_text.strip().splitlines()), 2)        # header + 1 incident
        self.assertIn("kids playing", csv_text)

    # ---------- System manager ----------

    def test_manager_pages_are_manager_only(self):
        for user in ("control", "officer1"):
            self.client.login(username=user, password="busguard123")
            self.assertEqual(self.client.get("/manage/").status_code, 403)
            self.assertEqual(self.client.get("/manage/accounts/").status_code, 403)
        self.client.login(username="manager", password="busguard123")
        self.assertRedirects(self.client.get("/"), "/manage/")
        for url in ("/manage/", "/manage/accounts/", "/manage/buses/", "/manage/buses/new/", "/control/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_manager_creates_edits_and_deletes_accounts(self):
        from django.contrib.auth.models import User
        self.client.login(username="manager", password="busguard123")
        r = self.client.post("/manage/accounts/new/", {"username": "officer9", "first_name": "Ana",
                                                       "last_name": "Lee", "role": "officer",
                                                       "phone": "0400 999", "password": "strongpass99"})
        self.assertRedirects(r, "/manage/accounts/")
        u = User.objects.get(username="officer9")
        self.assertEqual(u.profile.role, "officer")
        self.assertTrue(self.client.login(username="officer9", password="strongpass99"))
        self.client.login(username="manager", password="busguard123")
        # Duplicate username refused
        r = self.client.post("/manage/accounts/new/", {"username": "OFFICER9", "role": "officer", "password": "x" * 9})
        self.assertContains(r, "already taken")
        # Edit: make them a checker, keep password
        self.client.post(f"/manage/accounts/{u.pk}/", {"username": "officer9", "role": "checker", "password": ""})
        u.refresh_from_db()
        self.assertEqual(u.profile.role, "checker")
        self.assertTrue(u.check_password("strongpass99"))
        # Delete
        self.client.post(f"/manage/accounts/{u.pk}/delete/")
        self.assertFalse(User.objects.filter(username="officer9").exists())
        # Can't delete yourself
        me = User.objects.get(username="manager")
        self.client.post(f"/manage/accounts/{me.pk}/delete/")
        self.assertTrue(User.objects.filter(username="manager").exists())

    def test_station_with_fixed_location(self):
        self.client.login(username="manager", password="busguard123")
        r = self.client.post("/manage/buses/new/", {"name": "Darwin Interchange", "enabled": "on",
                                                    "kind": "area", "camera_type": "cctv",
                                                    "camera_address": "rtsp://10.0.0.5/stream",
                                                    "gps_source": "fixed",
                                                    "fixed_latitude": "-12.40", "fixed_longitude": "130.85"})
        bus = Bus.objects.get(name="Darwin Interchange")
        self.assertRedirects(r, f"/manage/buses/{bus.pk}/connect/")
        key = {"HTTP_X_BUS_KEY": bus.device_key}
        cfg = self.client.get("/api/bus/config/", **key).json()
        self.assertEqual((cfg["name"], cfg["camera_address"]), ("Darwin Interchange", "rtsp://10.0.0.5/stream"))
        # A photo sent with the key: the position is the fixed spot, whatever the camera sends
        with mock.patch("alerts.views.ai.score_frame", return_value=(0.1, "test")):
            self.client.post("/api/frame/", {"image": photo(), "latitude": "1", "longitude": "1"}, **key)
        bus.refresh_from_db()
        self.assertEqual((bus.latitude, bus.longitude), (-12.40, 130.85))
        # Wrong key / disabled / new key
        self.assertEqual(self.client.get("/api/bus/config/", HTTP_X_BUS_KEY="nope").status_code, 403)
        bus.enabled = False
        bus.save()
        self.assertEqual(self.client.get("/api/bus/config/", **key).status_code, 403)
        bus.enabled = True
        bus.save()
        self.client.post(f"/manage/buses/{bus.pk}/new-key/")
        self.assertEqual(self.client.get("/api/bus/config/", **key).status_code, 403)
        # Delete
        self.client.post(f"/manage/buses/{bus.pk}/delete/")
        self.assertFalse(Bus.objects.filter(name="Darwin Interchange").exists())

    def test_phone_camera_page(self):
        bus = self.bus
        self.assertEqual(self.client.get("/cam/wrong-key/").status_code, 404)
        r = self.client.get(f"/cam/{bus.device_key}/")
        self.assertContains(r, "Send alert now")
        self.assertNotContains(r, "isn't used")       # a phone camera uses the phone's GPS
        with mock.patch("alerts.views.ai.score_frame", return_value=(0.9, "test")):
            replies = [self.client.post("/api/frame/", {"image": photo(), "latitude": "-12.3711",
                                                        "longitude": "130.8686"}, **self.key).json() for _ in range(3)]
        self.assertTrue(replies[-1]["record"])
        bus.refresh_from_db()
        self.assertEqual((bus.latitude, bus.longitude), (-12.3711, 130.8686))
        # The phone's clip (webm from Android Chrome) becomes an incident at the phone's position
        video = SimpleUploadedFile("clip.webm", b"fake video", content_type="video/webm")
        with mock.patch("alerts.views.threading.Thread", RunNow), \
             mock.patch("alerts.views.ai.to_browser_mp4", side_effect=lambda p: p):
            self.client.post("/api/upload/", {"video": video, "latitude": "-12.3711", "longitude": "130.8686"},
                             **self.key)
        incident = Incident.objects.get()
        self.assertEqual((incident.bus_id, incident.latitude), ("Bus 4", -12.3711))
        # The manager's Connect page links to it
        self.client.login(username="manager", password="busguard123")
        self.assertContains(self.client.get(f"/manage/buses/{bus.pk}/connect/"), f"/cam/{bus.device_key}/")

    def test_clear_test_data(self):
        self.send_clip(manual=True)
        call_command("clear_test_data", "--yes", stdout=open(tempfile.mktemp(), "w"))
        self.assertFalse(Incident.objects.exists())
        self.assertFalse(Bus.objects.exists())
        self.assertTrue(User.objects.filter(username="manager").exists())

    def test_manual_alert(self):
        self.send_clip(manual=True)
        incident = Incident.objects.get()
        self.assertEqual(incident.status, Incident.Status.NEW)
        self.assertIsNone(incident.fight_score)

    def test_roles_are_separate(self):
        self.client.login(username="officer1", password="busguard123")
        self.assertEqual(self.client.get("/control/").status_code, 403)
        self.client.login(username="control", password="busguard123")
        self.assertEqual(self.client.get("/officer/").status_code, 403)
        self.assertEqual(self.client.get("/control/").status_code, 200)

    def test_dismiss(self):
        self.send_clip(manual=True)
        incident = Incident.objects.get()
        self.client.login(username="control", password="busguard123")
        self.client.post(f"/api/incident/{incident.pk}/dismiss/", {"note": "kids playing"})
        incident.refresh_from_db()
        self.assertEqual(incident.status, Incident.Status.DISMISSED)


def gtfs_feed(*vehicles):
    """A real GTFS-Realtime message, like a bus operator's feed, with these (id, lat, lon) vehicles."""
    from google.transit import gtfs_realtime_pb2
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(timezone.now().timestamp())
    for vid, lat, lon in vehicles:
        e = msg.entity.add()
        e.id = vid
        e.vehicle.vehicle.id = vid
        e.vehicle.position.latitude, e.vehicle.position.longitude = lat, lon
        e.vehicle.timestamp = msg.header.timestamp
    from types import SimpleNamespace
    return SimpleNamespace(content=msg.SerializeToString(), raise_for_status=lambda: None)


FEED = gtfs_feed(("NT-1043", -12.3742, 130.8818), ("NT-2210", -12.4805, 130.9835))


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class GpsFeedTest(TestCase):
    def setUp(self):
        setup_accounts()

    def test_bus_gps_tracker_through_the_feed(self):
        from alerts import gps_feed
        from alerts.models import GpsFeed
        self.client.login(username="manager", password="busguard123")
        page = self.client.get("/manage/buses/new/")
        self.assertContains(page, "2. Camera")
        self.assertContains(page, "Fleet GPS feed address")
        for removed in ("Simulated route", "Webcam", "demo"):
            self.assertNotContains(page, removed)
        self.assertEqual(self.client.get("/api/demo-gtfs-rt/").status_code, 404)
        base = {"name": "Bus 7", "kind": "bus", "enabled": "on"}
        # CCTV needs a stream address; the bus GPS tracker needs its ID and the feed address
        r = self.client.post("/manage/buses/new/", {**base, "camera_type": "cctv", "camera_address": "",
                                                    "gps_source": "feed", "vehicle_id": "", "feed-url": ""})
        self.assertContains(r, "live stream address")
        self.assertContains(r, "GPS tracker ID (its Vehicle ID")
        self.assertContains(r, "This field is required")
        for removed in ("simulated", "device"):
            r = self.client.post("/manage/buses/new/", {**base, "camera_type": "cctv", "camera_address": "rtsp://x",
                                                        "gps_source": removed})
            self.assertContains(r, "Select a valid choice")
        # A phone camera always uses the phone's GPS (no GPS choice needed)
        r = self.client.post("/manage/buses/new/", {"name": "Phone bus", "kind": "bus", "enabled": "on",
                                                    "camera_type": "phone"})
        phone_bus = Bus.objects.get(name="Phone bus")
        self.assertRedirects(r, f"/manage/buses/{phone_bus.pk}/connect/")
        self.assertEqual(phone_bus.gps_source, Bus.GpsSource.TRACKER)
        # Valid: CCTV camera + bus GPS tracker, saved together from one form
        with mock.patch("alerts.gps_feed.requests.get", return_value=FEED):
            r = self.client.post("/manage/buses/new/", {
                **base, "camera_type": "cctv", "camera_address": "rtsp://192.168.1.10:554/stream1",
                "gps_source": "feed", "vehicle_id": "nt-1043", "feed-url": "http://example.com/feed.pb"})
            bus = Bus.objects.get(name="Bus 7")
            self.assertRedirects(r, f"/manage/buses/{bus.pk}/connect/")
            self.assertEqual(GpsFeed.get().url, "http://example.com/feed.pb")
            bus.refresh_from_db()
            self.assertAlmostEqual(bus.latitude, -12.3742, places=4)         # read from the feed
            self.assertAlmostEqual(bus.longitude, 130.8818, places=4)
            # A camera can't move a bus that uses its GPS tracker
            with mock.patch("alerts.views.ai.score_frame", return_value=(0.1, "t")):
                self.client.post("/api/frame/", {"image": photo(), "latitude": "1", "longitude": "1"},
                                 HTTP_X_BUS_KEY=bus.device_key)
            bus.refresh_from_db()
            self.assertAlmostEqual(bus.latitude, -12.3742, places=4)
            # The camera program gets the camera address from the server
            cfg = self.client.get("/api/bus/config/", HTTP_X_BUS_KEY=bus.device_key).json()
            self.assertEqual((cfg["camera_type"], cfg["camera_address"]),
                             ("cctv", "rtsp://192.168.1.10:554/stream1"))
            self.assertContains(self.client.get(f"/manage/buses/{bus.pk}/connect/"), "rtsp://192.168.1.10:554/stream1")
            # Test GPS button: found / not found (lists the IDs in the feed)
            r = self.client.post(f"/manage/buses/{bus.pk}/test-gps/", follow=True)
            self.assertContains(r, "GPS connected")
            bus.vehicle_id = "BUS-999"
            bus.save()
            r = self.client.post(f"/manage/buses/{bus.pk}/test-gps/", follow=True)
            self.assertContains(r, "NT-2210")
            self.assertContains(r, "is not in the feed")
            ok, msg = gps_feed.poll_once()
            self.assertTrue(ok, msg)

    def test_bad_feed(self):
        from alerts import gps_feed
        from alerts.models import GpsFeed
        GpsFeed.objects.update_or_create(pk=GpsFeed.get().pk, defaults={"url": "http://example.com/f.pb",
                                                                      "enabled": True})
        with mock.patch("alerts.gps_feed.requests.get", side_effect=Exception("timed out")):
            ok, msg = gps_feed.poll_once()
        self.assertFalse(ok)
        self.assertIn("timed out", msg)
