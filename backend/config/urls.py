from django.conf import settings
from django.contrib import admin
from django.urls import path

from core import admin_login, public
from core.api import api
from core.auth import protected_resource_metadata

urlpatterns = [
    # No password login: the admin's login view starts the sandcorp-auth code flow.
    path(f"{settings.ADMIN_PATH}login/", admin_login.login_start, name="admin_login_start"),
    path(settings.ADMIN_PATH, admin.site.urls),
    path("auth/callback", admin_login.login_callback, name="auth_callback"),
    path(".well-known/oauth-protected-resource", protected_resource_metadata),
    path("api/", api.urls),
    # HTML shell routes: Django fills per-route meta, React takes over in the browser.
    path("", public.home, name="home"),
    path("a/<slug:slug>", public.article_detail, name="article"),
    path("sitemap.xml", public.sitemap),
    path("robots.txt", public.robots),
]
