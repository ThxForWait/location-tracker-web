from django.urls import path

from . import api, views

urlpatterns = [
    path('', views.graph_view, name='map'),
    path("api/packet/", api.api_packet, name="api-packet"),
]
