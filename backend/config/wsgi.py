import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
# web만 DB 읽기·쓰기를 제한한다(#228). gunicorn --timeout 30보다 짧아야 500·503이 나간다.
os.environ.setdefault("DB_IO_TIMEOUT", "5")

application = get_wsgi_application()
