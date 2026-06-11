from django.contrib import admin
from .models import Campana, Prospecto, EmailGenerado, ProspectoCRM, InteraccionCRM


@admin.register(Campana)
class CampanaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'producto', 'estado', 'total_prospectos', 'emails_enviados', 'respuestas', 'creada']
    list_filter = ['producto', 'estado']
    search_fields = ['nombre']


@admin.register(Prospecto)
class ProspectoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'apellido', 'empresa', 'email', 'estado', 'campana']
    list_filter = ['estado', 'campana']
    search_fields = ['nombre', 'apellido', 'empresa', 'email']


@admin.register(EmailGenerado)
class EmailGeneradoAdmin(admin.ModelAdmin):
    list_display = ['prospecto', 'asunto', 'aprobado', 'enviado_a_instantly', 'generado_en']
    list_filter = ['aprobado', 'enviado_a_instantly']


@admin.register(ProspectoCRM)
class ProspectoCRMAdmin(admin.ModelAdmin):
    list_display = ['prospecto', 'etapa', 'proxima_accion', 'fecha_proxima_accion', 'fecha_respuesta', 'actualizado']
    list_filter = ['etapa', 'proxima_accion']


@admin.register(InteraccionCRM)
class InteraccionCRMAdmin(admin.ModelAdmin):
    list_display = ['crm', 'creado']
    list_filter = ['creado']
