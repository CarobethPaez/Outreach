from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from apps.campaigns import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('campanas/nueva/', views.campana_nueva, name='campana_nueva'),
    path('campanas/<int:pk>/', views.campana_detalle, name='campana_detalle'),
    path('campanas/<int:pk>/importar/', views.importar_csv, name='importar_csv'),
    path('campanas/<int:pk>/generar/', views.generar_emails, name='generar_emails'),
    path('campanas/<int:pk>/enviar/', views.enviar_a_instantly, name='enviar_instantly'),
    path('campanas/<int:pk>/emails/', views.campana_emails, name='campana_emails'),
    path('campanas/<int:pk>/respuestas/', views.campana_respuestas, name='campana_respuestas'),
    path('campanas/<int:pk>/sincronizar/', views.sincronizar_respuestas, name='sincronizar'),
    path('campanas/<int:pk>/agregar-prospecto/', views.agregar_prospecto, name='agregar_prospecto'),
    path('campanas/<int:pk>/marcar-contactados/', views.marcar_contactados, name='marcar_contactados'),
    path('prospectos/<int:pk>/estado-respuesta/', views.actualizar_estado_respuesta, name='actualizar_estado_respuesta'),
    path('campanas/<int:campana_pk>/prospectos/<int:prospecto_pk>/', views.eliminar_prospecto, name='eliminar_prospecto'),
    path('emails/<int:pk>/aprobar/', views.aprobar_email, name='aprobar_email'),
    path('emails/<int:pk>/editar/', views.editar_email, name='editar_email'),
    path('crm/', views.crm, name='crm'),
    path('deck/', views.deck, name='deck'),
    path('ensayo/', views.ensayo, name='ensayo'),
    path('onboarding/', views.onboarding, name='onboarding'),
    path('test-instantly/', views.test_instantly, name='test_instantly'),
    path('login/', LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
]
