web: gunicorn shivazen.wsgi --bind 0.0.0.0:$PORT --workers 4 --threads 2 --log-file -
worker: celery -A shivazen worker --loglevel=info --concurrency=2
beat: celery -A shivazen beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
release: python manage.py migrate --noinput
