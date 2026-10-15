"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from albiontrackermap import account_views
from albiontrackermap.api_v1 import api as api_v1

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', api_v1.urls),
    path('accounts/', include('allauth.urls')),
    path('link/', account_views.link, name='link'),
    path('account/devices/', account_views.devices, name='devices'),
    path('map/', include('albiontrackermap.urls'))
]
