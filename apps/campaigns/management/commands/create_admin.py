import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Crea el superusuario inicial desde variables de entorno DJANGO_SUPERUSER_*'

    def handle(self, *args, **kwargs):
        User = get_user_model()
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin')
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@stock-wise.cloud')
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')

        if not password:
            self.stderr.write('DJANGO_SUPERUSER_PASSWORD no está definido.')
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(f'El usuario "{username}" ya existe.')
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f'Superusuario "{username}" creado correctamente.'))
