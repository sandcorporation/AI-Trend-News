"""The ninja API. Endpoints stay thin: they validate input and call the service layer."""

from django.db import connection
from django.db.utils import DatabaseError
from django.http import HttpRequest
from ninja import NinjaAPI, Schema, Status

from core.auth import install_auth_handlers

api = NinjaAPI(title="Sandcorp news API", docs_url=None)
# Protected endpoints use `auth=RequireScope("<scope>")` (core/auth.py).
install_auth_handlers(api)


class Health(Schema):
    status: str
    db: str


@api.get("/health", response={200: Health, 503: Health}, auth=None)
def health(request: HttpRequest) -> Status[Health]:
    """Liveness plus a real round trip to Postgres; the deploy waits on this."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return Status(503, Health(status="error", db="unavailable"))
    return Status(200, Health(status="ok", db="ok"))
