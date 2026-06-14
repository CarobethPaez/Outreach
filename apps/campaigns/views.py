import csv
import datetime
import io
import json
import logging
from functools import wraps
from pathlib import Path

import anthropic
import requests
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Campana, EmailGenerado, InteraccionCRM, Prospecto, ProspectoCRM

logger = logging.getLogger(__name__)


def _instantly_key():
    """Retorna la API key de Instantly sin espacios ni saltos de línea."""
    return str(settings.INSTANTLY_API_KEY).strip()


PRECIO_STOCKWISE_CLP = 115_000
PRECIO_STOCKMENU_CLP = 45_000

# Orden de etapas del pipeline para el funnel del dashboard. "perdido" no
# forma parte del funnel de conversión (se cuenta aparte si hace falta).
ETAPAS_FUNNEL = ['respondio', 'demo_agendada', 'demo_hecha', 'piloto_activo', 'cliente_pagado']


def _funnel_data(producto=None):
    """Calcula el funnel de conversión (contactados → ... → clientes pagados).

    Cada etapa del funnel es acumulativa: cuenta los contactos que llegaron
    a esa etapa o más allá, según su `etapa` actual en ProspectoCRM.
    """
    campanas = Campana.objects.all()
    crm_qs = ProspectoCRM.objects.all()
    if producto:
        campanas = campanas.filter(producto=producto)
        crm_qs = crm_qs.filter(prospecto__campana__producto=producto)

    contactados = sum(c.emails_enviados for c in campanas)

    conteo_por_etapa = {
        etapa: crm_qs.filter(etapa=etapa).count() for etapa, _ in ProspectoCRM.ETAPAS
    }

    etapas_funnel = []
    valor_anterior = contactados
    for i, etapa in enumerate(ETAPAS_FUNNEL):
        valor = sum(conteo_por_etapa[e] for e in ETAPAS_FUNNEL[i:])
        conversion = round(valor / valor_anterior * 100, 1) if valor_anterior else 0
        etapas_funnel.append({
            'etapa': etapa,
            'label': dict(ProspectoCRM.ETAPAS)[etapa],
            'valor': valor,
            'conversion': conversion,
        })
        valor_anterior = valor

    return {
        'contactados': contactados,
        'etapas': etapas_funnel,
        'pilotos_activos': conteo_por_etapa['piloto_activo'],
        'clientes_pagados': conteo_por_etapa['cliente_pagado'],
    }


def _superuser_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            return redirect('/login/')
        return view_func(request, *args, **kwargs)
    return wrapper


@_superuser_required
def dashboard(request):

    campanas = Campana.objects.all().order_by('-creada')

    total_enviados = sum(c.emails_enviados for c in campanas)
    total_respuestas = sum(c.respuestas for c in campanas)

    tasa_respuesta_global = round(total_respuestas / total_enviados * 100, 1) if total_enviados else 0

    funnel_global = _funnel_data()
    funnel_stockwise = _funnel_data('stockwise')
    funnel_stockmenu = _funnel_data('stockmenu')

    hoy = timezone.now().date()
    inicio_semana = hoy - datetime.timedelta(days=hoy.weekday())
    fin_semana = inicio_semana + datetime.timedelta(days=6)
    demos_agendadas_semana = ProspectoCRM.objects.filter(
        etapa='demo_agendada',
        fecha_proxima_accion__range=[inicio_semana, fin_semana],
    ).count()

    mrr_actual = (
        funnel_stockwise['clientes_pagados'] * PRECIO_STOCKWISE_CLP
        + funnel_stockmenu['clientes_pagados'] * PRECIO_STOCKMENU_CLP
    )
    mrr_potencial = (
        funnel_stockwise['pilotos_activos'] * PRECIO_STOCKWISE_CLP
        + funnel_stockmenu['pilotos_activos'] * PRECIO_STOCKMENU_CLP
    )

    return render(request, 'dashboard.html', {
        'campanas': campanas,
        'total_campanas': campanas.count(),
        'total_enviados': total_enviados,
        'total_respuestas': total_respuestas,
        'tasa_respuesta_global': tasa_respuesta_global,
        'funnel_global': funnel_global,
        'funnel_stockwise': funnel_stockwise,
        'funnel_stockmenu': funnel_stockmenu,
        'demos_agendadas_semana': demos_agendadas_semana,
        'pilotos_activos': funnel_stockwise['pilotos_activos'] + funnel_stockmenu['pilotos_activos'],
        'mrr_actual': mrr_actual,
        'mrr_potencial': mrr_potencial,
    })


@_superuser_required
def campana_nueva(request):

    if request.method == 'POST':
        campana = Campana.objects.create(
            nombre=request.POST['nombre'],
            producto=request.POST['producto'],
            tono=request.POST['tono'],
            num_followups=int(request.POST.get('num_followups', 2)),
            notas=request.POST.get('notas', ''),
        )
        messages.success(request, f'Campaña "{campana.nombre}" creada.')
        return redirect('campana_detalle', pk=campana.pk)

    return render(request, 'campana_nueva.html', {
        'productos': Campana.PRODUCTOS,
        'tonos': Campana.TONOS,
        'estados': Campana.ESTADOS,
    })


@_superuser_required
def campana_detalle(request, pk):

    campana = get_object_or_404(Campana, pk=pk)
    prospectos = campana.prospectos.select_related('email_generado').order_by('-creado')

    emails_aprobados_no_enviados = sum(
        1 for p in prospectos
        if hasattr(p, 'email_generado')
        and p.email_generado.aprobado
        and not p.email_generado.enviado_a_instantly
    )

    filtro_canal = request.GET.get('canal', '')
    if filtro_canal:
        prospectos = prospectos.filter(canal_contacto=filtro_canal)

    hay_no_contactados = campana.prospectos.filter(contactado=False).exists()

    return render(request, 'campana_detalle.html', {
        'campana': campana,
        'prospectos': prospectos,
        'emails_aprobados_no_enviados': emails_aprobados_no_enviados,
        'estados_prospecto': Prospecto.ESTADOS,
        'estados_respuesta': Prospecto.ESTADOS_RESPUESTA,
        'canales_prospecto': Prospecto.CANALES,
        'filtro_canal': filtro_canal,
        'hay_no_contactados': hay_no_contactados,
    })


@require_POST
def agregar_prospecto(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    campana = get_object_or_404(Campana, pk=pk)
    try:
        prospecto = Prospecto.objects.create(
            campana=campana,
            nombre=request.POST.get('nombre', '').strip(),
            apellido=request.POST.get('apellido', '').strip(),
            empresa=request.POST.get('empresa', '').strip(),
            cargo=request.POST.get('cargo', '').strip(),
            email=request.POST.get('email', '').strip(),
            linkedin_url=request.POST.get('linkedin_url', '').strip(),
            canal_contacto=request.POST.get('canal_contacto', ''),
            contactado=request.POST.get('contactado') == 'on',
            fuente='manual',
        )
        campana.total_prospectos = campana.prospectos.count()
        campana.save(update_fields=['total_prospectos'])
        messages.success(request, f'Prospecto "{prospecto.nombre_completo}" agregado.')
    except Exception as e:
        messages.error(request, f'Error al agregar prospecto: {e}')
    return redirect('campana_detalle', pk=pk)


@require_POST
def guardar_campaign_id(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    campana = get_object_or_404(Campana, pk=pk)
    campaign_id = request.POST.get('instantly_campaign_id', '').strip()
    campana.instantly_campaign_id = campaign_id
    campana.save(update_fields=['instantly_campaign_id'])
    return JsonResponse({'ok': True, 'instantly_campaign_id': campana.instantly_campaign_id})


@require_POST
def editar_campana_nombre(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    campana = get_object_or_404(Campana, pk=pk)
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            nuevo_nombre = data.get('nombre', '').strip()
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON inválido'}, status=400)
    else:
        nuevo_nombre = request.POST.get('nombre', '').strip()

    if not nuevo_nombre:
        return JsonResponse({'error': 'El nombre no puede estar vacío'}, status=400)

    campana.nombre = nuevo_nombre
    campana.save(update_fields=['nombre'])
    return JsonResponse({'ok': True, 'nombre': campana.nombre})



def eliminar_prospecto(request, campana_pk, prospecto_pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    prospecto = get_object_or_404(Prospecto, pk=prospecto_pk, campana__pk=campana_pk)
    campana = prospecto.campana
    prospecto.delete()

    campana.total_prospectos = campana.prospectos.count()
    campana.save(update_fields=['total_prospectos'])

    return JsonResponse({'ok': True, 'total_prospectos': campana.total_prospectos})


@require_POST
def actualizar_estado_respuesta(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    prospecto = get_object_or_404(Prospecto, pk=pk)
    nuevo_estado = request.POST.get('estado_respuesta', '')
    valores_validos = [v for v, _ in Prospecto.ESTADOS_RESPUESTA]
    if nuevo_estado not in valores_validos:
        return JsonResponse({'error': 'Estado inválido'}, status=400)

    prospecto.estado_respuesta = nuevo_estado
    prospecto.save(update_fields=['estado_respuesta'])
    return JsonResponse({'ok': True, 'label': prospecto.get_estado_respuesta_display()})


@require_POST
def marcar_contactados(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    campana = get_object_or_404(Campana, pk=pk)
    canal = request.POST.get('canal', '')
    filtro_canal = request.POST.get('filtro_canal', '')

    qs = campana.prospectos.filter(contactado=False)
    if filtro_canal:
        qs = qs.filter(canal_contacto=filtro_canal)

    canales_validos = [v for v, _ in Prospecto.CANALES]
    campos = {'contactado': True}
    if canal:
        if canal not in canales_validos:
            return JsonResponse({'error': 'Canal inválido'}, status=400)
        campos['canal_contacto'] = canal

    actualizados = qs.update(**campos)
    return JsonResponse({'actualizados': actualizados})


@require_POST
def importar_csv(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    campana = get_object_or_404(Campana, pk=pk)
    archivo = request.FILES.get('archivo')
    if not archivo:
        return JsonResponse({'error': 'No se recibió archivo'}, status=400)

    canal_csv = request.POST.get('canal_contacto', '')
    contactado_csv = request.POST.get('contactado') == '1'

    raw = archivo.read()
    try:
        contenido = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            contenido = raw.decode('latin-1')
        except UnicodeDecodeError:
            return JsonResponse({'error': 'El archivo tiene un encoding no soportado. Guárdalo como UTF-8.'}, status=400)
    reader = csv.DictReader(io.StringIO(contenido))

    creados = 0
    actualizados = 0
    errores = []

    COLUMNAS = {
        'First Name': 'nombre',
        'Last Name': 'apellido',
        'Title': 'cargo',
        'Company Name': 'empresa',
        'Industry': 'industria',
        'Email': 'email',
        'LinkedIn URL': 'linkedin_url',
        'City': 'ciudad',
        '# Employees': 'tamano_empresa',
    }

    for i, fila in enumerate(reader, start=2):
        try:
            email = fila.get('Email', '').strip()
            if not email:
                errores.append(f'Fila {i}: email vacío')
                continue

            datos = {
                campo_modelo: fila.get(col_csv, '').strip()
                for col_csv, campo_modelo in COLUMNAS.items()
                if col_csv != 'Email'
            }
            if canal_csv:
                datos['canal_contacto'] = canal_csv
            if contactado_csv:
                datos['contactado'] = True

            prospecto, creado = Prospecto.objects.get_or_create(
                campana=campana,
                email=email,
                defaults=datos,
            )
            if creado:
                creados += 1
            else:
                for campo, valor in datos.items():
                    setattr(prospecto, campo, valor)
                prospecto.save()
                actualizados += 1

        except Exception as e:
            errores.append(f'Fila {i}: {str(e)}')

    campana.total_prospectos = campana.prospectos.count()
    campana.save(update_fields=['total_prospectos'])

    return JsonResponse({
        'creados': creados,
        'actualizados': actualizados,
        'errores': errores,
    })


# Cada llamada a Claude (cuerpo + asunto) toma varios segundos. Procesar
# todos los prospectos de una campaña en un solo request puede superar
# el timeout de 60s del worker de Gunicorn y matar el request a medias.
# Por eso se procesa en lotes pequeños; el frontend llama repetidamente
# hasta que no queden prospectos pendientes.
GENERAR_EMAILS_LOTE = 5


@require_POST
def generar_emails(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    campana = get_object_or_404(Campana, pk=pk)
    total_campana = campana.prospectos.count()

    # Excluir prospectos ya contactados (marcados en verde en la UI) y
    # aquellos que ya tienen un email generado por IA — no tiene sentido
    # regenerar ni volver a contactar a quien ya fue abordado.
    prospectos_pendientes = campana.prospectos.filter(
        contactado=False,
        email_generado__isnull=True,
    )

    omitidos = total_campana - prospectos_pendientes.count()
    pendientes_antes = prospectos_pendientes.count()
    prospectos_a_procesar = list(prospectos_pendientes[:GENERAR_EMAILS_LOTE])

    generados = 0
    errores = []

    for prospecto in prospectos_a_procesar:
        try:
            _generar_email_claude(prospecto, campana)
            if prospecto.estado == 'pendiente':
                prospecto.estado = 'email_generado'
                prospecto.save(update_fields=['estado'])
            generados += 1
        except Exception as e:
            logger.error(f'Error generando email para prospecto ID {prospecto.pk}: {e}')
            errores.append(f'{prospecto.nombre_completo}: {str(e)}')

    campana.emails_generados = campana.prospectos.filter(estado='email_generado').count()
    campana.save(update_fields=['emails_generados'])

    restantes = max(0, pendientes_antes - len(prospectos_a_procesar))

    mensaje = (
        f'Procesados {len(prospectos_a_procesar)} de {pendientes_antes} prospectos pendientes. '
        f'{omitidos} prospectos ya contactados o con email ya generado fueron omitidos.'
    )
    logger.info(f'generar_emails campana={campana.pk}: {mensaje}')

    return JsonResponse({
        'generados': generados,
        'omitidos': omitidos,
        'restantes': restantes,
        'mensaje': mensaje,
        'errores': errores,
    })


def _generar_email_claude(prospecto, campana):
    cliente = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    if campana.producto == 'stockwise':
        prompt_cuerpo = (
            f"Redacta un email de prospección B2B en español para {prospecto.nombre} {prospecto.apellido}, "
            f"{prospecto.cargo} en {prospecto.empresa} ({prospecto.industria}, {prospecto.ciudad}). "
            f"Producto: StockWise — plataforma SaaS chilena que automatiza la gestión de inventario con IA. "
            f"El agente detecta escasez, contacta proveedores, negocia precios y genera órdenes de compra. "
            f"El cliente solo aprueba. Planes desde UF 3/mes. Trial gratis 7 días. "
            f"Tono: {campana.get_tono_display()}. Máximo 120 palabras. Sin asunto — solo el cuerpo. "
            f"CTA: proponer 15 minutos para mostrar el agente en acción. "
            f"URL: https://www.stock-wise.cloud "
            f"NO mencionar que es generado por IA. NO usar frases genéricas. "
            f"Cierra el email con esta firma exacta, respetando los saltos de línea:\n\n"
            f"Saludos,\n\n"
            f"Carolina Páez López\n"
            f"Creadora de StockWise\n"
            f"soporte@stock-wise.cloud\n"
            f"www.stock-wise.cloud"
        )
    else:
        prompt_cuerpo = (
            f"Redacta un email de prospección B2B en español para {prospecto.nombre} {prospecto.apellido}, "
            f"{prospecto.cargo} en {prospecto.empresa}. "
            f"{'Ubicación: ' + prospecto.ciudad + '.' if prospecto.ciudad else ''} "
            f"Producto: StockMenu — SaaS chileno para restaurantes y cafeterías. "
            f"Controla el stock de insumos en tiempo real, descuenta automáticamente con cada venta, "
            f"avisa por WhatsApp cuando algo está por agotarse, y emite boletas electrónicas al SII. "
            f"Plan Básico $29.990/mes. Plan Pro $39.990/mes. Sin contrato de permanencia. "
            f"Tono: {campana.get_tono_display()}. Máximo 100 palabras. Sin asunto — solo el cuerpo. "
            f"Personaliza mencionando el tipo de negocio ({prospecto.empresa}) de forma natural. "
            f"El dolor principal que resuelve: dejar de perder plata por sobrestock o quiebres de stock. "
            f"CTA: proponer 15 minutos para mostrar el sistema en acción con datos reales. "
            f"URL: https://stock-menu.com "
            f"NO mencionar que es generado por IA. NO usar frases genéricas como 'espero que estés bien'. "
            f"NO usar signos de exclamación. Escribir como persona real, no como vendedor. "
            f"Cierra el email con esta firma exacta, respetando los saltos de línea:\n\n"
            f"Saludos,\n\n"
            f"Carolina Páez\n"
            f"Creadora de StockMenu\n"
            f"soporte@stock-menu.com\n"
            f"stock-menu.com"
        )

    resp_cuerpo = cliente.messages.create(
        model='claude-sonnet-4-5',
        max_tokens=400,
        messages=[{'role': 'user', 'content': prompt_cuerpo}],
    )
    if not resp_cuerpo.content:
        raise Exception('Claude no devolvió contenido para el cuerpo del email')
    cuerpo = resp_cuerpo.content[0].text.strip()
    tokens_cuerpo = resp_cuerpo.usage.input_tokens + resp_cuerpo.usage.output_tokens

    prompt_asunto = (
        f"Genera SOLO el asunto del email (máximo 7 palabras, sin signos de exclamación, "
        f"sin 'Re:' ni 'Fwd:', que parezca escrito por una persona real, "
        f"en español) para este cuerpo:\n\n{cuerpo}"
    )
    resp_asunto = cliente.messages.create(
        model='claude-sonnet-4-5',
        max_tokens=50,
        messages=[{'role': 'user', 'content': prompt_asunto}],
    )
    if not resp_asunto.content:
        raise Exception('Claude no devolvió contenido para el asunto del email')
    asunto = resp_asunto.content[0].text.strip().strip('"').strip("'")
    tokens_asunto = resp_asunto.usage.input_tokens + resp_asunto.usage.output_tokens

    EmailGenerado.objects.update_or_create(
        prospecto=prospecto,
        defaults={
            'asunto': asunto,
            'cuerpo': cuerpo,
            'tokens_usados': tokens_cuerpo + tokens_asunto,
            'aprobado': False,
            'enviado_a_instantly': False,
        },
    )


@require_POST
def aprobar_email(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    email = get_object_or_404(EmailGenerado, pk=pk)
    email.aprobado = True
    email.save(update_fields=['aprobado'])

    email.prospecto.estado = 'aprobado'
    email.prospecto.save(update_fields=['estado'])

    return JsonResponse({'ok': True})


@require_POST
def aprobar_todos_emails(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)
    
    campana = get_object_or_404(Campana, pk=pk)

    # Approve all pending generated emails for this campaign — excluyendo
    # prospectos ya contactados (no tiene sentido aprobar/enviar a quien
    # ya fue abordado por otro canal).
    emails = EmailGenerado.objects.filter(
        prospecto__campana=campana,
        prospecto__contactado=False,
        aprobado=False,
        enviado_a_instantly=False
    )
    count = emails.count()
    emails.update(aprobado=True)

    # Update status of these prospects to approved
    prospectos = Prospecto.objects.filter(
        campana=campana,
        contactado=False,
        estado='email_generado'
    )
    prospectos.update(estado='aprobado')
    
    # Sync the count on campaign
    campana.emails_generados = campana.prospectos.filter(
        estado__in=['email_generado', 'aprobado', 'enviado', 'respondio', 'interesado']
    ).count()
    campana.save(update_fields=['emails_generados'])
    
    return JsonResponse({'ok': True, 'aprobados': count})


@require_POST
def editar_email(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    email = get_object_or_404(EmailGenerado, pk=pk)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    email.asunto = data.get('asunto', email.asunto)
    email.cuerpo = data.get('cuerpo', email.cuerpo)
    email.save(update_fields=['asunto', 'cuerpo', 'editado_en'])
    return JsonResponse({'ok': True})


@require_POST
def enviar_a_instantly(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    campana = get_object_or_404(Campana, pk=pk)

    if not campana.instantly_campaign_id:
        try:
            logger.info('[Instantly] Sin campaign_id — creando campaña en Instantly…')
            _crear_campana_instantly(campana)
            logger.info(f'[Instantly] Campaña creada. instantly_campaign_id="{campana.instantly_campaign_id}"')
        except Exception as e:
            logger.error(f'Error al crear campaña en Instantly: {e}')
            return JsonResponse({'enviados': 0, 'errores': [f'No se pudo crear la campaña en Instantly: {str(e)}']})

    emails_pendientes = EmailGenerado.objects.filter(
        prospecto__campana=campana,
        aprobado=True,
        enviado_a_instantly=False,
    ).select_related('prospecto')

    enviados = 0
    errores = []
    valid_emails = []

    for email in emails_pendientes:
        if not email.prospecto.email or not email.prospecto.email.strip():
            logger.error(f'[Instantly] Prospecto sin email: id={email.prospecto.pk} — omitido')
            errores.append(f'{email.prospecto.nombre_completo}: sin email')
            continue
        valid_emails.append(email)

    if not valid_emails:
        return JsonResponse({'enviados': 0, 'errores': errores})

    # Preparar lote de leads para envío masivo
    leads_payload = []
    for email in valid_emails:
        logger.info(
            f'[Instantly] Lead {email.prospecto.email} — '
            f'asunto: {email.asunto[:50] if email.asunto else "vacío"} — '
            f'cuerpo: {len(email.cuerpo) if email.cuerpo else 0} chars'
        )
        leads_payload.append({
            'email': email.prospecto.email.strip(),
            'first_name': email.prospecto.nombre or '',
            'last_name': email.prospecto.apellido or '',
            'company_name': email.prospecto.empresa or '',
            'personalization': email.cuerpo or '',
            'custom_variables': {
                'asunto': email.asunto or '',
                'cuerpo': email.cuerpo or '',
            }
        })

    headers = {
        'Authorization': f'Bearer {_instantly_key()}',
        'Content-Type': 'application/json',
    }
    payload = {
        'campaign_id': campana.instantly_campaign_id,
        'skip_if_in_workspace': False,
        'leads': leads_payload,
    }

    logger.info(f'[Instantly] POST /v2/leads/add (Batch) — enviando {len(leads_payload)} leads a la campaña {campana.instantly_campaign_id}')
    try:
        resp = requests.post(
            'https://api.instantly.ai/api/v2/leads/add',
            headers=headers,
            json=payload,
            timeout=30,
        )
        logger.info(f'[Instantly] Respuesta leads: status={resp.status_code}')
        logger.info(f'[Instantly] Respuesta leads body: {resp.text[:500]}')
        if resp.status_code != 200:
            error_detail = resp.text
            logger.error(f'[Instantly] Error {resp.status_code}: {error_detail}')
            raise Exception(f'Instantly {resp.status_code}: {error_detail}')

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            data = {}
        leads_creados = data if isinstance(data, list) else data.get('created_leads', [])

        # Mapear IDs de los leads por email
        lead_id_map = {}
        for lc in leads_creados:
            email_addr = lc.get('email')
            lead_id = lc.get('id')
            if email_addr and lead_id:
                lead_id_map[email_addr.lower().strip()] = lead_id

        # Actualizar base de datos
        for email in valid_emails:
            email_key = email.prospecto.email.lower().strip()
            if email_key in lead_id_map:
                email.prospecto.instantly_lead_id = lead_id_map[email_key]
            else:
                logger.warning(f'[Instantly] Lead no encontrado en respuesta para prospecto ID {email.prospecto.pk}')

            email.enviado_a_instantly = True
            email.save(update_fields=['enviado_a_instantly'])

            email.prospecto.estado = 'enviado'
            email.prospecto.save(update_fields=['estado', 'instantly_lead_id'])
            enviados += 1

    except Exception as e:
        logger.error(f'Error enviando lote a Instantly: {e}')
        errores.append(f'Error en envío por lotes: {str(e)}')

    with transaction.atomic():
        campana.emails_enviados = campana.prospectos.filter(estado='enviado').count()
        campana.save(update_fields=['emails_enviados'])

    return JsonResponse({'enviados': enviados, 'errores': errores})


def _crear_campana_instantly(campana):
    headers = {
        'Authorization': f'Bearer {_instantly_key()}',
        'Content-Type': 'application/json',
    }
    payload = {
        'name': campana.nombre,
        'campaign_schedule': {
            'schedules': [
                {
                    'name': 'StockWise Chile',
                    'timing': {'from': '09:00', 'to': '18:00'},
                    'days': {
                        'monday': True,
                        'tuesday': True,
                        'wednesday': True,
                        'thursday': True,
                        'friday': True,
                    },
                    'timezone': 'America/Santiago',
                }
            ]
        },
    }
    logger.info(f'[Instantly] POST /v2/campaigns — nombre="{campana.nombre}"')
    try:
        resp = requests.post(
            'https://api.instantly.ai/api/v2/campaigns',
            headers=headers,
            json=payload,
            timeout=15,
        )
        logger.info(f'[Instantly] Respuesta campaigns: status={resp.status_code} body={resp.text[:500]}')
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise Exception(f'Error de red al crear campaña en Instantly: {e}')

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as e:
        raise Exception(f'Respuesta inválida de Instantly al crear campaña: {e}')

    campaign_id = data.get('id') or data.get('campaign_id')
    if not campaign_id:
        raise Exception(f'Instantly no devolvió ID de campaña. Respuesta: {data}')

    campana.instantly_campaign_id = campaign_id
    campana.save(update_fields=['instantly_campaign_id'])

    # Nota: la cuenta de email se asigna desde el dashboard de Instantly,
    # no existe endpoint v2 para hacerlo via API.


@_superuser_required
def campana_emails(request, pk):

    campana = get_object_or_404(Campana, pk=pk)
    filtro = request.GET.get('filtro', '')

    emails = EmailGenerado.objects.filter(
        prospecto__campana=campana
    ).select_related('prospecto').order_by('-generado_en')

    if filtro == 'pendiente':
        emails = emails.filter(aprobado=False, enviado_a_instantly=False)
    elif filtro == 'aprobado':
        emails = emails.filter(aprobado=True, enviado_a_instantly=False)
    elif filtro == 'enviado':
        emails = emails.filter(enviado_a_instantly=True)

    return render(request, 'campana_emails.html', {
        'campana': campana,
        'emails': emails,
        'filtro': filtro,
    })


@_superuser_required
def campana_respuestas(request, pk):

    campana = get_object_or_404(Campana, pk=pk)
    respondieron = campana.prospectos.filter(
        estado__in=['respondio', 'interesado']
    ).order_by('-creado')

    return render(request, 'campana_respuestas.html', {
        'campana': campana,
        'respondieron': respondieron,
    })


@require_POST
def sincronizar_respuestas(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    campana = get_object_or_404(Campana, pk=pk)

    procesados = 0
    error_instantly = None

    # Bloque Instantly — solo aplica si la campaña tiene campaign_id asociado.
    if campana.instantly_campaign_id:
        headers = {'Authorization': f'Bearer {_instantly_key()}'}
        try:
            resp = requests.get(
                'https://api.instantly.ai/api/v2/replies',
                headers=headers,
                params={'campaign_id': campana.instantly_campaign_id},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f'[Instantly] Error al obtener respuestas: {e}')
            error_instantly = f'Error de conexión con Instantly: {str(e)}'
            data = None
        except (json.JSONDecodeError, ValueError):
            error_instantly = 'Respuesta inválida de Instantly'
            data = None

        if data is not None:
            for reply in data.get('replies', data if isinstance(data, list) else []):
                email_addr = reply.get('from_address') or reply.get('email', '')
                try:
                    prospecto = campana.prospectos.get(email=email_addr)
                    prospecto.estado = 'respondio'
                    prospecto.save(update_fields=['estado'])
                    ProspectoCRM.objects.get_or_create(prospecto=prospecto)
                    procesados += 1
                except Prospecto.DoesNotExist:
                    pass
                except Exception as e:
                    logger.error(f'Error procesando respuesta {email_addr}: {e}')

    # Prospectos marcados manualmente (vía dropdown "Estado respuesta") como
    # interesados, que respondieron por un canal distinto a Instantly y por
    # eso no aparecen en /v2/replies — también deben traspasarse al CRM.
    ESTADOS_RESPUESTA_INTERES = {
        'interesado': 'agendar_demo',
        'agendar_demo': 'agendar_demo',
        'pedir_mas_info': 'enviar_info',
    }
    interesados_manual = campana.prospectos.filter(
        estado_respuesta__in=ESTADOS_RESPUESTA_INTERES.keys(),
        prospectocrm__isnull=True,
    )
    for prospecto in interesados_manual:
        if prospecto.estado != 'interesado':
            prospecto.estado = 'interesado'
            prospecto.save(update_fields=['estado'])
        ProspectoCRM.objects.create(
            prospecto=prospecto,
            proxima_accion=ESTADOS_RESPUESTA_INTERES[prospecto.estado_respuesta],
        )
        procesados += 1

    campana.respuestas = campana.prospectos.filter(estado__in=['respondio', 'interesado']).count()
    campana.interesados = campana.prospectos.filter(estado='interesado').count()
    campana.save(update_fields=['respuestas', 'interesados'])

    respuesta = {'procesados': procesados}
    if error_instantly:
        respuesta['error_instantly'] = error_instantly
    return JsonResponse(respuesta)


@_superuser_required
def test_instantly(request):

    key = settings.INSTANTLY_API_KEY or ''
    key_preview = f"{key[:10]}...{key[-10:]}" if len(key) >= 20 else f"[muy corta: {len(key)} chars]"
    key_len = len(key)
    key_has_spaces = key != key.strip()

    try:
        resp = requests.get(
            'https://api.instantly.ai/api/v2/campaigns',
            headers={
                'Authorization': f'Bearer {key}',
                'Content-Type': 'application/json',
            },
            timeout=10,
        )
        status_code = resp.status_code
        try:
            body = resp.json()
        except Exception:
            body = resp.text
    except Exception as e:
        status_code = None
        body = str(e)

    lines = [
        '=== Diagnóstico Instantly API ===',
        '',
        f'KEY primeros/últimos 10 chars : {key_preview}',
        f'KEY longitud total            : {key_len} caracteres',
        f'KEY tiene espacios ocultos    : {"⚠️  SÍ" if key_has_spaces else "No"}',
        '',
        f'Status HTTP                  : {status_code}',
        f'Respuesta                    :',
        json.dumps(body, indent=2, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body),
    ]

    return JsonResponse({'diagnostico': '\n'.join(lines), 'status_code': status_code, 'body': body})


@_superuser_required
def deck(request):
    return render(request, 'deck.html')


DECK_PPTX_PATH = Path(__file__).resolve().parent / 'files' / 'StockWise_DeckVentas_v2.pptx'


@_superuser_required
def descargar_deck_pptx(request):
    if not DECK_PPTX_PATH.exists():
        raise Http404('Deck no encontrado')
    return FileResponse(
        open(DECK_PPTX_PATH, 'rb'),
        as_attachment=True,
        filename=DECK_PPTX_PATH.name,
        content_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
    )


@_superuser_required
def ensayo(request):
    return render(request, 'ensayo.html')


@_superuser_required
def onboarding(request):
    return render(request, 'onboarding.html')


@_superuser_required
def deck_stockmenu(request):
    return render(request, 'deck_stockmenu.html')


@_superuser_required
def ensayo_stockmenu(request):
    return render(request, 'ensayo_stockmenu.html')


@_superuser_required
def onboarding_stockmenu(request):
    return render(request, 'onboarding_stockmenu.html')


@_superuser_required
def crm(request):

    entradas = ProspectoCRM.objects.select_related(
        'prospecto', 'prospecto__campana'
    ).order_by('-fecha_respuesta')

    filtro_producto = request.GET.get('producto', '')
    if filtro_producto:
        entradas = entradas.filter(prospecto__campana__producto=filtro_producto)

    tarjetas_por_etapa = {etapa_key: [] for etapa_key, _ in ProspectoCRM.ETAPAS}
    for entrada in entradas:
        tarjetas_por_etapa.setdefault(entrada.etapa, []).append(entrada)

    columnas = [
        {'key': etapa_key, 'label': etapa_label, 'tarjetas': tarjetas_por_etapa[etapa_key]}
        for etapa_key, etapa_label in ProspectoCRM.ETAPAS
    ]

    total_interesados = entradas.count()
    demos_agendadas = entradas.filter(etapa='demo_agendada').count()
    clientes_cerrados = entradas.filter(etapa='cliente_pagado').count()

    return render(request, 'crm.html', {
        'columnas': columnas,
        'productos': Campana.PRODUCTOS,
        'etapas': ProspectoCRM.ETAPAS,
        'filtro_producto': filtro_producto,
        'total_interesados': total_interesados,
        'demos_agendadas': demos_agendadas,
        'clientes_cerrados': clientes_cerrados,
    })


@require_POST
@_superuser_required
def crm_agregar_contacto(request):
    nombre_completo = request.POST.get('nombre_completo', '').strip()
    empresa = request.POST.get('empresa', '').strip()
    cargo = request.POST.get('cargo', '').strip()
    email = request.POST.get('email', '').strip()
    producto = request.POST.get('producto', '')
    etapa = request.POST.get('etapa', 'respondio')
    proxima_accion = request.POST.get('proxima_accion', '').strip()
    fecha_proxima_accion = request.POST.get('fecha_proxima_accion') or None
    notas = request.POST.get('notas', '').strip()

    if not nombre_completo or not empresa or producto not in dict(Campana.PRODUCTOS):
        messages.error(request, 'Nombre completo, empresa y producto son obligatorios.')
        return redirect('crm')

    if etapa not in [v for v, _ in ProspectoCRM.ETAPAS]:
        etapa = 'respondio'

    partes = nombre_completo.split(' ', 1)
    nombre = partes[0]
    apellido = partes[1] if len(partes) > 1 else ''

    try:
        with transaction.atomic():
            campana_manual, _ = Campana.objects.get_or_create(
                nombre='Contactos manuales (CRM)',
                producto=producto,
                defaults={'estado': 'cerrada'},
            )

            prospecto = Prospecto.objects.create(
                campana=campana_manual,
                nombre=nombre,
                apellido=apellido,
                empresa=empresa,
                cargo=cargo,
                email=email,
                estado='respondio',
                fuente='manual',
            )
            campana_manual.total_prospectos = campana_manual.prospectos.count()
            campana_manual.save(update_fields=['total_prospectos'])

            ProspectoCRM.objects.create(
                prospecto=prospecto,
                etapa=etapa,
                proxima_accion=proxima_accion,
                fecha_proxima_accion=fecha_proxima_accion,
                notas=notas,
            )
        messages.success(request, f'Contacto "{nombre_completo}" agregado al CRM.')
    except Exception as e:
        messages.error(request, f'Error al agregar contacto: {e}')

    return redirect('crm')


@require_POST
@_superuser_required
def crm_mover_etapa(request, pk):
    entrada = get_object_or_404(ProspectoCRM, pk=pk)
    nueva_etapa = request.POST.get('etapa', '')
    valores_validos = [v for v, _ in ProspectoCRM.ETAPAS]
    if nueva_etapa in valores_validos:
        entrada.etapa = nueva_etapa
        entrada.save(update_fields=['etapa'])

    url = reverse('crm')
    filtro_producto = request.POST.get('producto', '')
    if filtro_producto:
        url += f'?producto={filtro_producto}'
    return redirect(url)


@_superuser_required
def crm_detalle(request, pk):
    entrada = get_object_or_404(
        ProspectoCRM.objects.select_related('prospecto', 'prospecto__campana'), pk=pk
    )

    if request.method == 'POST':
        accion = request.POST.get('accion', '')
        if accion == 'nota':
            texto = request.POST.get('texto', '').strip()
            if texto:
                InteraccionCRM.objects.create(crm=entrada, texto=texto)
        elif accion == 'actualizar':
            nueva_etapa = request.POST.get('etapa', '')
            if nueva_etapa in [v for v, _ in ProspectoCRM.ETAPAS]:
                entrada.etapa = nueva_etapa
            nueva_accion = request.POST.get('proxima_accion', '')
            if nueva_accion in [v for v, _ in ProspectoCRM.ACCIONES]:
                entrada.proxima_accion = nueva_accion
            entrada.fecha_proxima_accion = request.POST.get('fecha_proxima_accion') or None
            entrada.save()
            messages.success(request, 'Ficha actualizada.')
        return redirect('crm_detalle', pk=pk)

    return render(request, 'crm_detalle.html', {
        'entrada': entrada,
        'interacciones': entrada.interacciones.all(),
        'acciones': ProspectoCRM.ACCIONES,
        'etapas': ProspectoCRM.ETAPAS,
    })


@_superuser_required
def agenda(request):
    hoy = timezone.now().date()
    limite = hoy + datetime.timedelta(days=7)

    pendientes = ProspectoCRM.objects.select_related(
        'prospecto', 'prospecto__campana'
    ).filter(
        fecha_proxima_accion__isnull=False,
        fecha_proxima_accion__lte=limite,
    ).order_by('fecha_proxima_accion')

    items = []
    for entrada in pendientes:
        items.append({
            'entrada': entrada,
            'vencido': entrada.fecha_proxima_accion < hoy,
            'es_hoy': entrada.fecha_proxima_accion == hoy,
        })

    contactos_crm = ProspectoCRM.objects.select_related('prospecto').order_by('prospecto__nombre')

    return render(request, 'agenda.html', {
        'items': items,
        'hoy': hoy,
        'contactos_crm': contactos_crm,
    })


@require_POST
@_superuser_required
def agenda_agregar_seguimiento(request):
    crm_id = request.POST.get('crm_id', '')
    proxima_accion = request.POST.get('proxima_accion', '').strip()
    fecha_proxima_accion = request.POST.get('fecha_proxima_accion') or None

    entrada = get_object_or_404(ProspectoCRM, pk=crm_id)

    if not proxima_accion or not fecha_proxima_accion:
        messages.error(request, 'Acción y fecha son obligatorias.')
        return redirect('agenda')

    entrada.proxima_accion = proxima_accion
    entrada.fecha_proxima_accion = fecha_proxima_accion
    entrada.save(update_fields=['proxima_accion', 'fecha_proxima_accion'])
    messages.success(request, f'Seguimiento agendado para "{entrada.prospecto.nombre_completo}".')
    return redirect('agenda')
