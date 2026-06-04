import csv
import io
import json
import logging

import anthropic
import requests
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Campana, EmailGenerado, Prospecto, ProspectoCRM

logger = logging.getLogger(__name__)

PRECIO_STOCKWISE_CLP = 115_000
PRECIO_STOCKMENU_CLP = 45_000


def _superuser_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            return redirect('/login/')
        return view_func(request, *args, **kwargs)
    return wrapper


def dashboard(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return redirect('/login/')

    campanas = Campana.objects.all().order_by('-creada')

    total_enviados = sum(c.emails_enviados for c in campanas)
    total_respuestas = sum(c.respuestas for c in campanas)
    total_interesados = sum(c.interesados for c in campanas)

    interesados_sw = sum(c.interesados for c in campanas if c.producto == 'stockwise')
    interesados_sm = sum(c.interesados for c in campanas if c.producto == 'stockmenu')

    mrr_potencial_stockwise = interesados_sw * PRECIO_STOCKWISE_CLP
    mrr_potencial_stockmenu = interesados_sm * PRECIO_STOCKMENU_CLP

    return render(request, 'dashboard.html', {
        'campanas': campanas,
        'total_campanas': campanas.count(),
        'total_enviados': total_enviados,
        'total_respuestas': total_respuestas,
        'total_interesados': total_interesados,
        'mrr_potencial_stockwise': mrr_potencial_stockwise,
        'mrr_potencial_stockmenu': mrr_potencial_stockmenu,
    })


def campana_nueva(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return redirect('/login/')

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


def campana_detalle(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return redirect('/login/')

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

    campos = {'contactado': True}
    if canal:
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

    contenido = archivo.read().decode('utf-8-sig')
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


@require_POST
def generar_emails(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    campana = get_object_or_404(Campana, pk=pk)
    todos_los_prospectos = campana.prospectos.all()

    generados = 0
    errores = []

    for prospecto in todos_los_prospectos:
        try:
            _generar_email_claude(prospecto, campana)
            if prospecto.estado == 'pendiente':
                prospecto.estado = 'email_generado'
                prospecto.save(update_fields=['estado'])
            generados += 1
        except Exception as e:
            logger.error(f'Error generando email para {prospecto.email}: {e}')
            errores.append(f'{prospecto.email}: {str(e)}')

    campana.emails_generados = campana.prospectos.filter(estado='email_generado').count()
    campana.save(update_fields=['emails_generados'])

    return JsonResponse({'generados': generados, 'errores': errores})


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
            f"Redacta un email de prospección B2B en español para {prospecto.nombre}, "
            f"dueño/gerente de {prospecto.empresa} en {prospecto.ciudad}. "
            f"Producto: StockMenu — SaaS chileno para gastronomía con menú QR digital, POS integrado, "
            f"gestión de inventario y análisis IA. Reemplaza la carta impresa y el POS caro. "
            f"Tono: {campana.get_tono_display()}. Máximo 120 palabras. Sin asunto — solo el cuerpo. "
            f"CTA: mostrar cómo quedaría su carta digital en 10 minutos. "
            f"URL: stockmenu.cl "
            f"NO mencionar que es generado por IA. "
            f"Cierra el email con esta firma exacta, respetando los saltos de línea:\n\n"
            f"Saludos,\n\n"
            f"Carolina Páez López\n"
            f"Creadora de StockWise\n"
            f"soporte@stock-wise.cloud\n"
            f"www.stock-wise.cloud"
        )

    resp_cuerpo = cliente.messages.create(
        model='claude-sonnet-4-5',
        max_tokens=400,
        messages=[{'role': 'user', 'content': prompt_cuerpo}],
    )
    cuerpo = resp_cuerpo.content[0].text.strip()
    tokens_cuerpo = resp_cuerpo.usage.input_tokens + resp_cuerpo.usage.output_tokens

    prompt_asunto = (
        f"Genera SOLO el asunto del email (máximo 8 palabras, sin signos de exclamación, "
        f"que parezca escrito por una persona real) para este cuerpo:\n\n{cuerpo}"
    )
    resp_asunto = cliente.messages.create(
        model='claude-sonnet-4-5',
        max_tokens=50,
        messages=[{'role': 'user', 'content': prompt_asunto}],
    )
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
def editar_email(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    email = get_object_or_404(EmailGenerado, pk=pk)
    data = json.loads(request.body)
    email.asunto = data.get('asunto', email.asunto)
    email.cuerpo = data.get('cuerpo', email.cuerpo)
    email.save(update_fields=['asunto', 'cuerpo', 'editado_en'])
    return JsonResponse({'ok': True})


@require_POST
def enviar_a_instantly(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    campana = get_object_or_404(Campana, pk=pk)
    emails_pendientes = EmailGenerado.objects.filter(
        prospecto__campana=campana,
        aprobado=True,
        enviado_a_instantly=False,
    ).select_related('prospecto')

    enviados = 0
    errores = []

    for email in emails_pendientes:
        try:
            _crear_lead_instantly(email.prospecto, email, campana)
            email.enviado_a_instantly = True
            email.save(update_fields=['enviado_a_instantly'])
            email.prospecto.estado = 'enviado'
            email.prospecto.save(update_fields=['estado'])
            enviados += 1
        except Exception as e:
            logger.error(f'Error enviando a Instantly {email.prospecto.email}: {e}')
            errores.append(f'{email.prospecto.email}: {str(e)}')

    campana.emails_enviados = campana.prospectos.filter(estado='enviado').count()
    campana.save(update_fields=['emails_enviados'])

    return JsonResponse({'enviados': enviados, 'errores': errores})


def _crear_lead_instantly(prospecto, email_generado, campana):
    if not campana.instantly_campaign_id:
        _crear_campana_instantly(campana)

    headers = {
        'Authorization': f'Bearer {settings.INSTANTLY_API_KEY}',
        'Content-Type': 'application/json',
    }
    payload = {
        'campaign_id': campana.instantly_campaign_id,
        'email': prospecto.email,
        'first_name': prospecto.nombre,
        'last_name': prospecto.apellido,
        'company_name': prospecto.empresa,
        'personalization': email_generado.cuerpo,
        'custom_variables': {
            'asunto_personalizado': email_generado.asunto,
        },
    }
    resp = requests.post(
        'https://api.instantly.ai/api/v2/leads',
        headers=headers,
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get('id'):
        prospecto.instantly_lead_id = data['id']
        prospecto.save(update_fields=['instantly_lead_id'])


def _crear_campana_instantly(campana):
    headers = {
        'Authorization': f'Bearer {settings.INSTANTLY_API_KEY}',
        'Content-Type': 'application/json',
    }
    payload = {
        'name': campana.nombre,
        'sending_account': settings.INSTANTLY_EMAIL,
    }
    resp = requests.post(
        'https://api.instantly.ai/api/v2/campaigns',
        headers=headers,
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    campana.instantly_campaign_id = data['id']
    campana.save(update_fields=['instantly_campaign_id'])


def campana_emails(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return redirect('/login/')

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


def campana_respuestas(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return redirect('/login/')

    campana = get_object_or_404(Campana, pk=pk)
    respondieron = campana.prospectos.filter(
        estado__in=['respondio', 'interesado']
    ).order_by('-creado')

    return render(request, 'campana_respuestas.html', {
        'campana': campana,
        'respondieron': respondieron,
    })


def sincronizar_respuestas(request, pk):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Sin permiso'}, status=403)

    campana = get_object_or_404(Campana, pk=pk)

    headers = {'Authorization': f'Bearer {settings.INSTANTLY_API_KEY}'}
    resp = requests.get(
        'https://api.instantly.ai/api/v2/replies',
        headers=headers,
        params={'campaign_id': campana.instantly_campaign_id},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    procesados = 0
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

    campana.respuestas = campana.prospectos.filter(estado__in=['respondio', 'interesado']).count()
    campana.save(update_fields=['respuestas'])

    return JsonResponse({'procesados': procesados})


def deck(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return redirect('/login/')
    return render(request, 'deck.html')


def ensayo(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return redirect('/login/')
    return render(request, 'ensayo.html')


def onboarding(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return redirect('/login/')
    return render(request, 'onboarding.html')


def crm(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return redirect('/login/')

    entradas = ProspectoCRM.objects.select_related(
        'prospecto', 'prospecto__campana'
    ).order_by('-fecha_respuesta')

    filtro_producto = request.GET.get('producto', '')
    filtro_accion = request.GET.get('accion', '')

    if filtro_producto:
        entradas = entradas.filter(prospecto__campana__producto=filtro_producto)
    if filtro_accion:
        entradas = entradas.filter(proxima_accion=filtro_accion)

    if request.method == 'POST':
        entrada_id = request.POST.get('entrada_id')
        entrada = get_object_or_404(ProspectoCRM, pk=entrada_id)
        entrada.proxima_accion = request.POST.get('proxima_accion', entrada.proxima_accion)
        entrada.notas = request.POST.get('notas', entrada.notas)
        if request.POST.get('interesado') == '1':
            entrada.prospecto.estado = 'interesado'
            entrada.prospecto.save(update_fields=['estado'])
            entrada.prospecto.campana.interesados = entrada.prospecto.campana.prospectos.filter(
                estado='interesado'
            ).count()
            entrada.prospecto.campana.save(update_fields=['interesados'])
        entrada.save()
        messages.success(request, 'Registro actualizado.')
        return redirect('crm')

    total_interesados = entradas.filter(prospecto__estado='interesado').count()
    demos_agendadas = entradas.filter(proxima_accion='agendar_demo').count()
    clientes_cerrados = entradas.filter(proxima_accion='cerrado').count()

    return render(request, 'crm.html', {
        'entradas': entradas,
        'acciones': ProspectoCRM.ACCIONES,
        'productos': Campana.PRODUCTOS,
        'filtro_producto': filtro_producto,
        'filtro_accion': filtro_accion,
        'total_interesados': total_interesados,
        'demos_agendadas': demos_agendadas,
        'clientes_cerrados': clientes_cerrados,
    })
