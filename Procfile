web: DJANGO_ENV=prod gunicorn clinica.wsgi --bind 0.0.0.0:$PORT --workers 4 --threads 2 --log-file -
worker: DJANGO_ENV=prod celery -A clinica worker --loglevel=info --concurrency=2
beat: DJANGO_ENV=prod celery -A clinica beat --loglevel=info
release: DJANGO_ENV=prod python manage.py migrate --noinput
