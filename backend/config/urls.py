from django.conf import settings
from django.contrib import admin
from django.urls import path

from core import admin_login, public
from core.api import api
from core.auth import protected_resource_metadata
from mcpserver.views import mcp_endpoint

urlpatterns = [
    # No password login: the admin's login view starts the sandcorp-auth code flow.
    path(f"{settings.ADMIN_PATH}login/", admin_login.login_start, name="admin_login_start"),
    path(settings.ADMIN_PATH, admin.site.urls),
    path("auth/callback", admin_login.login_callback, name="auth_callback"),
    path(".well-known/oauth-protected-resource", protected_resource_metadata),
    # RFC 9728 path-suffixed metadata URL for the /mcp resource: same document (as in portfolio).
    path(".well-known/oauth-protected-resource/mcp", protected_resource_metadata),
    path("mcp", mcp_endpoint, name="mcp"),
    path("api/", api.urls),
    # HTML shell routes: Django fills per-route meta, React takes over in the browser.
    path("", public.home, name="home"),
    path("a/<slug:slug>", public.article_detail, name="article"),
    path("contact", public.contact, name="contact"),
    path("predictions", public.predictions_page, name="predictions"),
    path("go/sandcorp", public.go_sandcorp, name="go_sandcorp"),
    path("sitemap.xml", public.sitemap),
    path("feed.xml", public.feed),
    path("robots.txt", public.robots),
]
