from django.urls import path

from . import manage_views as m
from . import views

app_name = "alerts"

urlpatterns = [
    path("", views.home, name="home"),

    # Bus camera
    path("api/frame/", views.frame, name="frame"),
    path("api/upload/", views.upload, name="upload"),
    path("api/bus/config/", views.bus_config, name="bus_config"),
    path("cam/<str:key>/", views.phone_camera, name="phone_camera"),

    # Control room
    path("control/", views.control_room, name="control"),
    path("api/control/feed/", views.control_feed, name="control_feed"),
    path("api/incident/<int:pk>/officers/", views.nearby_officers, name="nearby_officers"),
    path("api/incident/<int:pk>/dismiss/", views.dismiss, name="dismiss"),
    path("api/incident/<int:pk>/assign/", views.assign, name="assign"),
    path("api/ai/retrain/", views.start_retrain, name="retrain"),
    path("api/ai/status/", views.ai_status, name="ai_status"),

    # Field officer
    path("officer/", views.officer_screen, name="officer"),
    path("api/officer/location/", views.officer_location, name="officer_location"),
    path("api/officer/feed/", views.officer_feed, name="officer_feed"),
    path("api/incident/<int:pk>/on-way/", views.officer_on_way, name="on_way"),
    path("api/incident/<int:pk>/resolve/", views.officer_resolve, name="resolve"),

    # System manager
    path("manage/", m.dashboard, name="manage"),
    path("manage/ai/", m.ai_improvement, name="manage_ai"),
    path("manage/history/", m.incident_history, name="manage_history"),
    path("manage/history/<int:pk>/", m.incident_detail, name="manage_incident"),
    path("manage/accounts/", m.account_list, name="manage_accounts"),
    path("manage/accounts/new/", m.account_edit, name="manage_account_new"),
    path("manage/accounts/<int:pk>/", m.account_edit, name="manage_account_edit"),
    path("manage/accounts/<int:pk>/delete/", m.account_delete, name="manage_account_delete"),
    path("manage/buses/", m.bus_list, name="manage_buses"),
    path("manage/buses/new/", m.bus_edit, name="manage_bus_new"),
    path("manage/buses/<int:pk>/", m.bus_edit, name="manage_bus_edit"),
    path("manage/buses/<int:pk>/connect/", m.bus_connect, name="manage_bus_connect"),
    path("manage/buses/<int:pk>/test-gps/", m.bus_test_gps, name="manage_bus_test_gps"),
    path("manage/buses/<int:pk>/new-key/", m.bus_new_key, name="manage_bus_new_key"),
    path("manage/buses/<int:pk>/delete/", m.bus_delete, name="manage_bus_delete"),
]
