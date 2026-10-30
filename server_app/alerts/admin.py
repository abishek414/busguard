from django.contrib import admin

from .models import Bus, Incident, Profile


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ("id", "bus_id", "created_at", "status", "fight_score", "assigned_officer")
    list_filter = ("status", "bus_id")


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "phone", "on_duty", "latitude", "longitude", "location_updated_at")
    list_filter = ("role", "on_duty")


@admin.register(Bus)
class BusAdmin(admin.ModelAdmin):
    list_display = ("name", "last_seen", "last_score", "latitude", "longitude", "last_alert_at")
