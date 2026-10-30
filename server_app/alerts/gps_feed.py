"""
Fleet GPS feed: get every bus's real position from the bus operator's tracking system.

Bus tracking systems (the ones behind "where's my bus" apps) publish vehicle positions in
the standard GTFS-Realtime format: a feed listing each vehicle's ID and latitude/longitude,
updated every few seconds. The system manager enters the feed address and each bus's GPS
tracker ID on the bus's page; BusGuard then keeps those buses' positions up to date.
"""
import logging
import math
import threading
import time

import requests
from django.db import close_old_connections
from django.utils import timezone

from .models import Bus, GpsFeed

log = logging.getLogger("alerts")
_poll_lock = threading.Lock()


# ---------- Reading a feed ----------

def fetch_vehicles(feed):
    """Download the feed. Returns a list of {"id", "label", "latitude", "longitude", "timestamp"}."""
    from google.transit import gtfs_realtime_pb2

    headers = {feed.header_name: feed.header_value} if feed.header_name else {}
    r = requests.get(feed.url, headers=headers, timeout=15)
    r.raise_for_status()
    message = gtfs_realtime_pb2.FeedMessage()
    message.ParseFromString(r.content)
    vehicles = []
    for entity in message.entity:
        if not entity.HasField("vehicle") or not entity.vehicle.HasField("position"):
            continue
        v = entity.vehicle
        vehicles.append({
            "id": v.vehicle.id or entity.id,
            "label": v.vehicle.label,
            "latitude": v.position.latitude,
            "longitude": v.position.longitude,
            "timestamp": v.timestamp or None,
        })
    return vehicles


def match_vehicle(bus, vehicles):
    """Find this bus in the feed by its Vehicle ID (matches the feed's vehicle id or label)."""
    want = bus.vehicle_id.strip().lower()
    if not want:
        return None
    for v in vehicles:
        if want in (str(v["id"]).lower(), str(v["label"]).lower()):
            return v
    return None


def poll_once():
    """Read the feed once and update every bus that uses it. Returns (ok, message)."""
    feed = GpsFeed.get()
    if not feed.url:
        return False, "No feed address set"
    try:
        vehicles = fetch_vehicles(feed)
    except Exception as e:
        feed.last_checked_at, feed.last_ok = timezone.now(), False
        feed.last_message = f"Could not read the feed: {e}"[:255]
        feed.save()
        log.warning("GPS feed: %s", feed.last_message)
        return False, feed.last_message

    updated = 0
    for bus in Bus.objects.filter(gps_source=Bus.GpsSource.FEED).exclude(vehicle_id=""):
        v = match_vehicle(bus, vehicles)
        if v:
            bus.latitude, bus.longitude = v["latitude"], v["longitude"]
            bus.location_updated_at = timezone.now()
            bus.save(update_fields=["latitude", "longitude", "location_updated_at"])
            updated += 1
    feed.last_checked_at, feed.last_ok = timezone.now(), True
    feed.last_vehicle_count = len(vehicles)
    feed.last_message = f"{len(vehicles)} vehicles in the feed, {updated} BusGuard buses updated"
    feed.save()
    return True, feed.last_message


def maybe_poll():
    """Called often (every control room refresh). Reads the feed in the background
    when it's due, so pages never wait for it."""
    feed = GpsFeed.objects.filter(pk=1, enabled=True).first()
    if not feed or not feed.url:
        return
    if feed.last_checked_at and (timezone.now() - feed.last_checked_at).total_seconds() < feed.poll_seconds:
        return
    if not _poll_lock.acquire(blocking=False):
        return  # already reading it

    def run():
        try:
            close_old_connections()
            poll_once()
        except Exception:
            log.exception("GPS feed poll failed")
        finally:
            close_old_connections()
            _poll_lock.release()
    threading.Thread(target=run, daemon=True).start()
