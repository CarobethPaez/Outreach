web: python manage.py migrate && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 60 --worker-class sync
