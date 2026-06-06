from django.core.management.base import BaseCommand
from apps.campaigns.models import Campana


class Command(BaseCommand):
    help = 'Crea la campaña de StockMenu para restaurantes y cafeterías'

    def handle(self, *args, **kwargs):
        campana, created = Campana.objects.get_or_create(
            nombre='StockMenu — Restaurantes y Cafeterías Chile',
            defaults={
                'producto': 'stockmenu',
                'estado': 'borrador',
                'tono': 'directo',
                'num_followups': 2,
                'notas': (
                    'Segmento: dueños y administradores de restaurantes, '
                    'cafeterías y locales gastronómicos en Chile. '
                    'Hook principal: control de stock automático + '
                    'boleta electrónica SII integrada. '
                    'Precio: Plan Básico $29.990/mes, Plan Pro $39.990/mes. '
                    'Demo: stock-menu.com/demo/'
                ),
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(
                f'Campaña creada: "{campana.nombre}" (id={campana.pk})'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'La campaña ya existe (id={campana.pk})'
            ))
