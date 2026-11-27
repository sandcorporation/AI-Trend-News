import os

from django.core.wsgi import get_wsgi_application

from config import database

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
# web만 DB 읽기·쓰기를 제한한다(#228). gunicorn --timeout 30보다 짧아야 500·503이 나간다.
database.web_io_timeout = int(os.environ.get("WEB_DB_IO_TIMEOUT") or 5)

application = get_wsgi_application()
