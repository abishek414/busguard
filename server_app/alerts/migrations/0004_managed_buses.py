import secrets

from django.db import migrations, models

import alerts.models


def give_each_bus_a_key(apps, schema_editor):
    Bus = apps.get_model("alerts", "Bus")
    for bus in Bus.objects.all():
        bus.device_key = secrets.token_urlsafe(12)
        bus.save(update_fields=["device_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("alerts", "0003_self_learning"),
    ]

    operations = [
        migrations.AlterField(
            model_name="profile",
            name="role",
            field=models.CharField(choices=[("manager", "System manager"), ("checker", "Control room checker"),
                                            ("officer", "Field officer")], default="officer", max_length=20),
        ),
        # 1) add the key without "unique", 2) give every existing bus its own key, 3) make it unique
        migrations.AddField(
            model_name="bus",
            name="device_key",
            field=models.CharField(max_length=40, null=True),
        ),
        migrations.RunPython(give_each_bus_a_key, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="bus",
            name="device_key",
            field=models.CharField(default=alerts.models.new_device_key, max_length=40, unique=True),
        ),
        migrations.AddField(
            model_name="bus",
            name="enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="bus",
            name="notes",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="bus",
            name="gps_source",
            field=models.CharField(choices=[("simulated", "Simulated route (demo)"),
                                            ("device", "GPS of the bus computer"),
                                            ("tracker", "Phone on the bus (GPS tracker page)"),
                                            ("fixed", "Fixed location")], default="simulated", max_length=20),
        ),
        migrations.AddField(
            model_name="bus",
            name="route",
            field=models.CharField(blank=True, choices=[("casuarina-city", "Casuarina to Darwin City"),
                                                        ("palmerston-city", "Palmerston to Darwin City")],
                                   default="casuarina-city", max_length=40),
        ),
        migrations.AddField(
            model_name="bus",
            name="route_speed",
            field=models.PositiveIntegerField(default=40, help_text="km/h, simulated route only"),
        ),
        migrations.AddField(
            model_name="bus",
            name="fixed_latitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="bus",
            name="fixed_longitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="bus",
            name="location_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
