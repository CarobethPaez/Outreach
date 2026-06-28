from django.db import models
from django.db.models import Q


class Campana(models.Model):
    PRODUCTOS = [('stockwise', 'StockWise')]
    ESTADOS = [
        ('borrador', 'Borrador'),
        ('activa', 'Activa'),
        ('pausada', 'Pausada'),
        ('cerrada', 'Cerrada'),
    ]
    TONOS = [
        ('directo', 'Directo y al grano'),
        ('consultivo', 'Consultivo'),
        ('curioso', 'Curioso/pregunta'),
    ]

    nombre = models.CharField(max_length=150)
    producto = models.CharField(max_length=20, choices=PRODUCTOS)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='borrador')
    instantly_campaign_id = models.CharField(max_length=200, blank=True)
    tono = models.CharField(max_length=20, choices=TONOS, default='directo')
    num_followups = models.IntegerField(default=2)
    total_prospectos = models.IntegerField(default=0)
    emails_generados = models.IntegerField(default=0)
    emails_enviados = models.IntegerField(default=0)
    respuestas = models.IntegerField(default=0)
    interesados = models.IntegerField(default=0)
    creada = models.DateTimeField(auto_now_add=True)
    notas = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Campaña'
        verbose_name_plural = 'Campañas'
        ordering = ['-creada']

    def __str__(self):
        return f"{self.nombre} ({self.get_producto_display()})"

    @property
    def tasa_respuesta(self):
        if self.emails_enviados == 0:
            return 0
        return round(self.respuestas / self.emails_enviados * 100, 1)


class Prospecto(models.Model):
    ESTADOS = [
        ('pendiente', 'Pendiente'),
        ('email_generado', 'Email generado'),
        ('aprobado', 'Aprobado'),
        ('enviado', 'Enviado a Instantly'),
        ('respondio', 'Respondió'),
        ('interesado', 'Interesado'),
        ('no_interesado', 'No interesado'),
        ('descartado', 'Descartado'),
    ]
    CANALES = [
        ('linkedin', 'LinkedIn'),
        ('rrss', 'RRSS'),
        ('telefono', 'Teléfono'),
        ('email', 'Email'),
    ]
    FUENTES = [
        ('apollo', 'Apollo'),
        ('manual', 'Manual'),
    ]
    ESTADOS_RESPUESTA = [
        ('sin_respuesta', 'Sin respuesta'),
        ('interesado', 'Interesado ✓'),
        ('no_interesado', 'No interesado'),
        ('no_interesado_sap', 'No interesado — usa SAP'),
        ('no_interesado_sistema', 'No interesado — tiene sistema'),
        ('pedir_mas_info', 'Pidió más información'),
        ('agendar_demo', 'Quiere demo'),
        ('no_responde', 'No responde'),
    ]

    campana = models.ForeignKey(Campana, on_delete=models.CASCADE, related_name='prospectos')
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100, blank=True)
    cargo = models.CharField(max_length=150, blank=True)
    empresa = models.CharField(max_length=150)
    industria = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    linkedin_url = models.URLField(blank=True)
    ciudad = models.CharField(max_length=100, blank=True)
    tamano_empresa = models.CharField(max_length=50, blank=True)
    estado = models.CharField(max_length=30, choices=ESTADOS, default='pendiente', verbose_name='Estado email')
    canal_contacto = models.CharField(max_length=20, choices=CANALES, blank=True, default='')
    contactado = models.BooleanField(default=False)
    fuente = models.CharField(max_length=20, choices=FUENTES, default='apollo')
    estado_respuesta = models.CharField(
        max_length=30, choices=ESTADOS_RESPUESTA, default='sin_respuesta',
        verbose_name='Estado respuesta'
    )
    instantly_lead_id = models.CharField(max_length=200, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Prospecto'
        verbose_name_plural = 'Prospectos'
        constraints = [
            models.UniqueConstraint(
                fields=['campana', 'email'],
                condition=~Q(email=''),
                name='unique_campana_email_nonempty',
            )
        ]

    def __str__(self):
        return f"{self.nombre} {self.apellido} — {self.empresa}"

    @property
    def nombre_completo(self):
        return f"{self.nombre} {self.apellido}".strip()


class EmailGenerado(models.Model):
    prospecto = models.OneToOneField(
        Prospecto, on_delete=models.CASCADE, related_name='email_generado'
    )
    asunto = models.CharField(max_length=200)
    cuerpo = models.TextField()
    tokens_usados = models.IntegerField(default=0)
    aprobado = models.BooleanField(default=False)
    enviado_a_instantly = models.BooleanField(default=False)
    generado_en = models.DateTimeField(auto_now_add=True)
    editado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Email generado'
        verbose_name_plural = 'Emails generados'

    def __str__(self):
        return f"Email para {self.prospecto.nombre_completo}"


class ProspectoCRM(models.Model):
    ACCIONES = [
        ('agendar_demo', 'Agendar demo'),
        ('enviar_info', 'Enviar más info'),
        ('llamar', 'Llamar'),
        ('seguimiento', 'Seguimiento'),
        ('cerrado', 'Cerrado - cliente'),
    ]
    ETAPAS = [
        ('respondio', 'Respondió'),
        ('demo_agendada', 'Demo agendada'),
        ('demo_hecha', 'Demo hecha'),
        ('piloto_activo', 'Piloto activo'),
        ('cliente_pagado', 'Cliente pagado'),
        ('perdido', 'Perdido'),
    ]

    prospecto = models.OneToOneField(Prospecto, on_delete=models.CASCADE)
    proxima_accion = models.CharField(max_length=30, choices=ACCIONES, default='agendar_demo')
    etapa = models.CharField(max_length=30, choices=ETAPAS, default='respondio')
    fecha_proxima_accion = models.DateField(null=True, blank=True)
    notas = models.TextField(blank=True)
    fecha_respuesta = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'CRM Prospecto'
        verbose_name_plural = 'CRM Prospectos'
        ordering = ['-fecha_respuesta']

    def __str__(self):
        return f"CRM: {self.prospecto.nombre_completo}"


class InteraccionCRM(models.Model):
    crm = models.ForeignKey(ProspectoCRM, on_delete=models.CASCADE, related_name='interacciones')
    texto = models.TextField()
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Interacción CRM'
        verbose_name_plural = 'Interacciones CRM'
        ordering = ['-creado']

    def __str__(self):
        return f"Nota para {self.crm.prospecto.nombre_completo} — {self.creado:%d/%m/%Y}"
