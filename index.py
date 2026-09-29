from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json
import os
import re
import ssl
import subprocess
import time
import urllib.request

# ==========================================
# CONFIGURACIÓN GENERAL
# ==========================================
TOLERANCIA_MINUTOS = 12
ZONA_CHILE = ZoneInfo("America/Santiago")
ARCHIVO_HISTORIAL = "historial_presion_punta_arenas.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
}

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ==========================================
# ESTACIONES DIRECTEMAR (PUNTA ARENAS)
# ==========================================
ESTACIONES_DIRECTEMAR = [
    {
        "nombre": "Capitanía de Puerto Edén",
        "url": "http://web.directemar.cl/met/jturno/estaciones/eden/index.htm",
        "lat": -49.133,
        "lon": -74.433,
    },
    {
        "nombre": "Capitanía de Puerto Puerto Natales",
        "url": "http://web.directemar.cl/met/jturno/estaciones/natales/index.htm",
        "lat": -51.733,
        "lon": -72.500,
    },
    {
        "nombre": "Alcaldía de Mar Paso Timbales",
        "url": "http://web.directemar.cl/met/jturno/estaciones/timbales/index.htm",
        "lat": -52.283,
        "lon": -70.083,
    },
    {
        "nombre": "Alcaldía de Mar Puerto Navarino",
        "url": "http://web.directemar.cl/met/jturno/estaciones/navarino/index.htm",
        "lat": -54.950,
        "lon": -68.316,
    },
    {
        "nombre": "Alcaldía de Mar Puerto Corrientes",
        "url": "http://web.directemar.cl/met/jturno/estaciones/corrientes/index.htm",
        "lat": -53.983,
        "lon": -70.216,
    },
]

# ==========================================
# ESTACIONES EXTERNAS / WEATHERLINK
# ==========================================
ESTACIONES_EXTERNAS = [
    {
        "nombre": "Asmar Magallanes",
        "url": "https://weatherlink.com/embeddablePage/show/aa24908f6c68472ba163e55765986705",
        "lat": -53.150,
        "lon": -70.916,
        "tipo": "weatherlink"
    }
]

ORDEN_ESTACIONES = [
    "Capitanía de Puerto Edén",
    "Capitanía de Puerto Puerto Natales",
    "Alcaldía de Mar Paso Timbales",
    "Alcaldía de Mar Puerto Navarino",
    "Alcaldía de Mar Puerto Corrientes",
    "Asmar Magallanes",
]

def obtener_hora_chile():
    return datetime.now(ZONA_CHILE)

def convertir_numero(valor):
    if valor is None:
        return None
    try:
        val_str = str(valor).strip()
        if any(c in val_str.lower() for c in ["color", "purple", "line", "data", "{", "}"]):
            return None
        return float(val_str.replace(",", "."))
    except (ValueError, TypeError):
        return None

def formatear_direccion(dir_str):
    if not dir_str:
        return ""
    d = str(dir_str).upper().strip()
    if any(c in d.lower() for c in ["color", "purple", "line", "data", "{", "}"]):
        return ""
    if len(d) == 3:
        return f"{d[0]}/{d[1:]}"
    return d

def grados_a_cardinal(grados):
    if grados is None:
        return "N/D"
    direcciones = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    indice = int((grados + 11.25) / 22.5) % 16
    return formatear_direccion(direcciones[indice])

def gestionar_historial_presion(nombre_estacion, presion_actual):
    ahora = obtener_hora_chile()
    historial = {}
    if os.path.exists(ARCHIVO_HISTORIAL):
        try:
            with open(ARCHIVO_HISTORIAL, "r", encoding="utf-8") as f:
                historial = json.load(f)
        except Exception:
            historial = {}

    if nombre_estacion not in historial:
        historial[nombre_estacion] = []

    registros = historial[nombre_estacion]
    registros.append({"t": ahora.timestamp(), "p": presion_actual})

    limite_tiempo = ahora.timestamp() - (3.5 * 3600)
    registros = [r for r in registros if r["t"] >= limite_tiempo]
    historial[nombre_estacion] = registros

    try:
        with open(ARCHIVO_HISTORIAL, "w", encoding="utf-8") as f:
            json.dump(historial, f)
    except Exception:
        pass

    if presion_actual is None:
        return ""

    objetivo_t = ahora.timestamp() - (3 * 3600)
    candidatos = [r for r in registros if abs(r["t"] - objetivo_t) <= (45 * 60)]

    if not candidatos:
        candidatos_antiguos = [r for r in registros if r["t"] <= objetivo_t + 1800]
        if candidatos_antiguos:
            presion_pasada = candidatos_antiguos[0]["p"]
        else:
            return ""
    else:
        candidatos.sort(key=lambda x: abs(x["t"] - objetivo_t))
        presion_pasada = candidatos[0]["p"]

    if presion_pasada is None:
        return ""

    dif = presion_actual - presion_pasada

    if dif > 0.2:
        return " ↗"
    elif dif < -0.2:
        return " ↘"
    else:
        return " ➔"

def consultar_directemar(est):
    try:
        req = urllib.request.Request(est["url"], headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            html = response.read().decode("utf-8", errors="ignore")
            
            texto_plano = re.sub(r'<[^>]+>', ' ', html)
            texto_plano = texto_plano.replace('\xa5', ' ').replace('\xa0', ' ').replace('&nbsp;', ' ').replace('&deg;', '°').replace('&#176;', '°')
            texto_plano = re.sub(r'\s+', ' ', texto_plano).strip()

            temp, pres, viento, dir_viento, racha, precipitacion = "--", "--", "--", "", "--", "--"
            pres_val = None

            temp_match = re.search(r'(?:Temperatura|Temperature)\s*[:]?\s*([\-]?\d+(?:[.,]\d+)?)', texto_plano, re.IGNORECASE)
            if temp_match:
                val = convertir_numero(temp_match.group(1))
                if val is not None:
                    temp = f"{val:.1f}°C"

            pres_match = re.search(r'(?:Barometer|Presi[oó]n)[^\d]*([\-]?\d+(?:[.,]\d+)?)\s*(?:hPa|mb)?', texto_plano, re.IGNORECASE)
            if pres_match:
                pres_val = convertir_numero(pres_match.group(1))
                if pres_val is not None:
                    tendencia = gestionar_historial_presion(est["nombre"], pres_val)
                    pres = f"{pres_val:.1f} hPa{tendencia}"

            bearing_match = re.search(r'Wind\s*Bearing[^\d]*\d+(?:[.,]\d+)?\s*°?\s*([N,S,E,W]{1,3})', texto_plano, re.IGNORECASE)
            if not bearing_match:
                bearing_match = re.search(r'(?:Direcci[oó]n\s*Viento|Wind\s*Direction)[^\w]*([N,S,E,W]{1,3})', texto_plano, re.IGNORECASE)
            if not bearing_match:
                bearing_match = re.search(r'(?:Direcci[oó]n|Dir)[^\w]*(?:del\s*)?(?:Viento)?[^\w]*([N,S,E,W]{1,3})', texto_plano, re.IGNORECASE)
            if not bearing_match:
                deg_match = re.search(r'(?:Direcci[oó]n|Dir|Wind\s*Direction|Bearing)[^\d]*(\d+(?:[.,]\d+)?)\s*°', texto_plano, re.IGNORECASE)
                if deg_match:
                    grados_val = convertir_numero(deg_match.group(1))
                    if grados_val is not None:
                        dir_viento = grados_a_cardinal(grados_val)

            if bearing_match and not dir_viento:
                dir_viento = formatear_direccion(bearing_match.group(1))

            viento_match = re.search(r'Wind\s*Speed\s*\(avg\)[^\d]*(\d+(?:[.,]\d+)?)\s*(?:kts|kt|knots|nudos)?', texto_plano, re.IGNORECASE)
            if not viento_match:
                viento_match = re.search(r'Wind\s*Speed[^\d]*(\d+(?:[.,]\d+)?)\s*(?:kts|kt|knots|nudos)?', texto_plano, re.IGNORECASE)
            if not viento_match:
                viento_match = re.search(r'Viento[^\d]*(\d+(?:[.,]\d+)?)\s*(?:kts|kt|knots|nudos)?', texto_plano, re.IGNORECASE)
            
            if viento_match:
                val = convertir_numero(viento_match.group(1))
                if val is not None:
                    viento = f"{val:.1f} kt"

            racha_match = re.search(r'(?:Wind\s*Speed\s*\(gust\)|Gust|Racha|Ráfaga|Rafaga)[^\d]*(\d+(?:[.,]\d+)?)\s*(?:kts|kt|knots|nudos)?', texto_plano, re.IGNORECASE)
            if racha_match:
                val = convertir_numero(racha_match.group(1))
                if val is not None:
                    racha = f"{val:.1f} kt"

            pp_match = re.search(r'Rainfall[\s\-_]+today[^\d]*(\d+(?:[.,]\d+)?)', texto_plano, re.IGNORECASE)
            if not pp_match:
                pp_match = re.search(r'(?:Precipitaci[oó]n|Lluvia|Rain|Precip)[^\d]*(\d+(?:[.,]\d+)?)', texto_plano, re.IGNORECASE)
            if pp_match:
                val = convertir_numero(pp_match.group(1))
                if val is not None:
                    precipitacion = f"{val:.1f} mm"

            match_fecha = re.search(r'(?:Page\s+updated|Actualizado)\s+(\d{1,2}-\d{1,2}-\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?)', texto_plano, re.IGNORECASE)
            if not match_fecha:
                return False, "SIN DATOS VÁLIDOS", "N/D", temp, pres, viento, dir_viento, racha, precipitacion

            fecha_str = match_fecha.group(1)
            partes_f = fecha_str.split()
            if len(partes_f) == 2:
                fecha_p, hora_p = partes_f
                sub_hora = hora_p.split(":")
                if len(sub_hora[0]) == 1:
                    sub_hora[0] = "0" + sub_hora[0]
                    fecha_str = f"{fecha_p} {':'.join(sub_hora)}"

            formato_fecha = "%d-%m-%Y %H:%M:%S" if fecha_str.count(":") == 2 else "%d-%m-%Y %H:%M"
            fecha_estacion = datetime.strptime(fecha_str, formato_fecha).replace(tzinfo=ZONA_CHILE)
            dif_min = abs((obtener_hora_chile() - fecha_estacion).total_seconds() / 60)

            if dif_min <= TOLERANCIA_MINUTOS or (170 <= dif_min <= 200):
                return True, "OPERATIVA", fecha_str, temp, pres, viento, dir_viento, racha, precipitacion
            else:
                return False, f"DESACTUALIZADA ({int(dif_min)} min)", fecha_str, temp, pres, viento, dir_viento, racha, precipitacion

    except Exception as e:
        print(f"Error Directemar {est['nombre']}: {e}")
        return False, "SIN CONEXIÓN", "Error de red", "--", "--", "--", "", "--", "--"

def consultar_asmar_magallanes(est):
    try:
        req = urllib.request.Request(est["url"], headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12, context=ctx) as response:
            html = response.read().decode("utf-8", errors="ignore")
            texto_plano = re.sub(r'<[^>]+>', ' ', html)
            texto_plano = re.sub(r'\s+', ' ', texto_plano).strip()

            temp, pres, viento, dir_viento, racha, precipitacion = "--", "--", "--", "", "--", "--"
            pres_val = None

            temp_match = re.search(r'([\-]?\d+(?:[.,]\d+)?)\s*°C', texto_plano)
            if temp_match:
                val = convertir_numero(temp_match.group(1))
                if val is not None:
                    temp = f"{val:.1f}°C"

            pres_match = re.search(r'(?:Barometer|Barómetro)[^\d]*([\d]+[.,]\d+)\s*hPa', texto_plano, re.IGNORECASE)
            if pres_match:
                pres_val = convertir_numero(pres_match.group(1))
                if pres_val is not None:
                    tendencia = gestionar_historial_presion(est["nombre"], pres_val)
                    pres = f"{pres_val:.1f} hPa{tendencia}"

            viento_match = re.search(r'Wind[^\d]*([\d]+[.,]\d+)\s*(?:knots|kts|kt|nudos)?\s*([N,S,E,W]{1,3})?', texto_plano, re.IGNORECASE)
            if viento_match:
                val = convertir_numero(viento_match.group(1))
                if val is not None:
                    viento = f"{val:.1f} kt"
                if viento_match.group(2):
                    dir_viento = formatear_direccion(viento_match.group(2))

            racha_match = re.search(r'(?:gust|racha)[^\d]*([\d]+[.,]\d+)', texto_plano, re.IGNORECASE)
            if racha_match:
                val = convertir_numero(racha_match.group(1))
                if val is not None:
                    racha = f"{val:.1f} kt"

            rain_match = re.search(r'Rain[^\d]*([\d]+[.,]\d+)\s*mm', texto_plano, re.IGNORECASE)
            if rain_match:
                val = convertir_numero(rain_match.group(1))
                if val is not None:
                    precipitacion = f"{val:.1f} mm"

            es_valido = (pres_val is not None or temp != "--")
            estado_txt = "OPERATIVA" if es_valido else "SIN DATOS VÁLIDOS"
            ultimo_str = obtener_hora_chile().strftime("%d-%m-%Y %H:%M")

            return es_valido, estado_txt, ultimo_str, temp, pres, viento, dir_viento, racha, precipitacion

    except Exception as e:
        print(f"Error en Asmar Magallanes: {e}")
        return False, "SIN CONEXIÓN", "Error de red", "--", "--", "--", "", "--", "--"

def generar_html(resultados_totales, hay_alerta):
    total_estaciones = len(resultados_totales)
    operativas = sum(1 for r in resultados_totales if r['ok'] is True)

    markers_js = ""
    for r in resultados_totales:
        color = "green" if r['ok'] else "red"
        dir_txt = f" ({r['dir_viento']})" if r['dir_viento'] else ""
        popup_txt = f"<b>{r['nombre']}</b><br>Estado: {r['estado']}<br>Temp: {r['temp']} | Viento: {r['viento']}{dir_txt} | Racha: {r['racha']} | Pres: {r['pres']} | Lluvia: {r['precipitacion']}<br>Reporte: {r['ultimo']}<br><a href='{r['url']}' target='_blank'>Abrir enlace ↗</a>"
        
        markers_js += f"""
        L.circleMarker([{r['lat']}, {r['lon']}], {{
            color: '{color}', fillColor: '{color}', fillOpacity: 0.8, radius: 9
        }}).addTo(map).bindPopup("{popup_txt}");
        """

    cards_html = ""
    for r in resultados_totales:
        clase = "ok" if r['ok'] else "error"
        icono = "🔴" if not r['ok'] else "🟢"
        footer_texto = f"Reporte: {r['ultimo']}"

        if r['dir_viento']:
            viento_contenido = f'<span style="display: block; font-size: 0.58em; color: var(--wind-color, #1d4ed8); font-weight: 800; line-height: 1.1;">🌬️ {r["dir_viento"]}</span><span style="display: block; font-size: 0.72em; font-weight: 700; line-height: 1.1;">{r["viento"]}</span>'
        else:
            viento_contenido = f'<span style="display: block; font-size: 0.58em; color: transparent; font-weight: 800; line-height: 1.1; user-select: none;">-</span><span style="display: block; font-size: 0.72em; font-weight: 700; line-height: 1.1;">{r["viento"]}</span>'

        cuerpo_tarjeta = f"""
            <div class="card-body-content">
                <div class="row-top">
                    <div class="item-box temp-box">🌡️ {r['temp']}</div>
                    <div class="item-box">{viento_contenido}</div>
                    <div class="item-box"><span style="font-size: 0.58em; color: var(--wind-color, #1d4ed8); font-weight: 800; display: block; line-height: 1.1;">💨 RACHA</span><span style="font-size: 0.72em; font-weight: 700; line-height: 1.1;">{r['racha']}</span></div>
                </div>
                <div class="row-bottom">
                    <div class="item-box"><span style="font-size: 0.68em; font-weight: 700;">⏲️ {r['pres']}</span></div>
                    <div class="item-box"><span style="font-size: 0.68em; font-weight: 700;">🌧️ {r['precipitacion']}</span></div>
                </div>
            </div>
        """

        cards_html += f"""
        <a href="{r['url']}" target="_blank" class="card-link">
            <div class="card {clase}">
                <div class="card-header">
                    <span class="station-name">{r['nombre']}</span>
                    <span class="status-badge">{icono}</span>
                </div>
                {cuerpo_tarjeta}
                <div class="card-footer-info">
                    <span class="time">{footer_texto}</span>
                    <span class="click-text">Ver ↗</span>
                </div>
            </div>
        </a>
        """

    alerta_class = "alerta-activa" if hay_alerta else ""
    alerta_banner = '<div class="banner-alerta">⚠️ ¡ATENCIÓN: HAY ESTACIONES CON FALLAS O DESACTUALIZADAS! ⚠️</div>' if hay_alerta else ""
    hora_actual_chile = obtener_hora_chile().strftime("%d-%m-%Y %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="30">
    <title>Monitor de Estaciones Automáticas - Punta Arenas</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        :root {{
            --bg-color: #f4f6f9;
            --text-color: #1e293b;
            --h1-color: #0f2942;
            --sub2-color: #1e40af;
            --sub-color: #64748b;
            --summary-bg: #ffffff;
            --summary-border: #cbd5e1;
            --summary-text: #0f2942;
            --card-ok-bg: linear-gradient(135deg, #dbeafe 0%, #cbd5e1 55%, #94a3b8 100%);
            --card-ok-border: #94a3b8;
            --card-error-bg: linear-gradient(135deg, #fee2e2 0%, #fecaca 55%, #f87171 100%);
            --card-error-border: #f87171;
            --station-name-color: #0f172a;
            --item-bg: rgba(255, 255, 255, 0.9);
            --item-border: rgba(255, 255, 255, 0.95);
            --item-text: #0f172a;
            --time-color: #334155;
            --click-color: #1d4ed8;
            --wind-color: #1d4ed8;
            --footer-border: rgba(255, 255, 255, 0.4);
        }}

        body.dark-mode {{
            --bg-color: #121212;
            --text-color: #e0e0e0;
            --h1-color: #ffffff;
            --sub2-color: #60a5fa;
            --sub-color: #94a3b8;
            --summary-bg: #1e1e1e;
            --summary-border: #333333;
            --summary-text: #ffffff;
            --card-ok-bg: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            --card-ok-border: #334155;
            --card-error-bg: linear-gradient(135deg, #450a0a 0%, #7f1d1d 100%);
            --card-error-border: #991b1b;
            --station-name-color: #f8fafc;
            --item-bg: rgba(30, 41, 59, 0.85);
            --item-border: rgba(51, 65, 85, 0.8);
            --item-text: #f1f5f9;
            --time-color: #cbd5e1;
            --click-color: #60a5fa;
            --wind-color: #60a5fa;
            --footer-border: rgba(255, 255, 255, 0.1);
        }}

        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: var(--bg-color); color: var(--text-color); padding: 15px; margin: 0; transition: background-color 0.3s ease, color 0.3s ease; }}
        h1 {{ text-align: center; color: var(--h1-color); margin-bottom: 0; font-size: 22px; line-height: 1.2; font-weight: 700; }}
        .subtitle-line2 {{ text-align: center; color: var(--sub2-color); margin-bottom: 6px; font-size: 16px; font-weight: bold; }}
        .subtitle {{ text-align: center; color: var(--sub-color); margin-bottom: 12px; font-size: 12px; }}
        .summary {{ text-align: center; font-weight: bold; margin-bottom: 15px; color: var(--summary-text); font-size: 14px; background: var(--summary-bg); padding: 6px 16px; border-radius: 20px; max-width: 280px; margin-left: auto; margin-right: auto; box-shadow: 0 2px 6px rgba(0,0,0,0.06); border: 1px solid var(--summary-border); }}
        
        @keyframes parpadeoFondo {{ 
            0% {{ background-color: var(--bg-color); }} 
            50% {{ background-color: #fca5a5; }} 
            100% {{ background-color: var(--bg-color); }} 
        }}
        body.alerta-activa {{ animation: parpadeoFondo 1.5s infinite; }}
        
        body.dark-mode.alerta-activa {{ 
            animation: none !important; 
            background-color: #121212 !important; 
        }}

        .banner-alerta {{ background: linear-gradient(135deg, #ef4444, #dc2626); color: white; text-align: center; font-weight: bold; padding: 10px; border-radius: 8px; margin-bottom: 15px; font-size: 14px; box-shadow: 0 4px 12px rgba(239, 68, 68, 0.3); }}
        #map {{ height: 350px; width: 100%; max-width: 1200px; margin: 0 auto 20px auto; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.08); border: 1px solid var(--summary-border); }}
        
        .grid {{ 
            display: grid; 
            grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); 
            gap: 15px; 
            max-width: 1200px; 
            margin: 0 auto; 
            align-items: stretch; 
        }}
        
        .card-link {{ text-decoration: none; color: inherit; display: flex; flex-direction: column; height: 100%; }}
        .card {{ 
            border-radius: 14px; 
            padding: 10px 10px; 
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08); 
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            height: 100%; 
            box-sizing: border-box;
        }}
        .card.ok {{ 
            background: var(--card-ok-bg); 
            border: 1px solid var(--card-ok-border);
            border-left: 6px solid #16a34a; 
        }}
        .card.error {{ 
            background: var(--card-error-bg); 
            border: 1px solid var(--card-error-border);
            border-left: 6px solid #dc2626; 
        }}
        .card:hover {{ 
            transform: translateY(-3px); 
            box-shadow: 0 8px 20px rgba(30, 64, 175, 0.2); 
        }}
        .card.error:hover {{
            box-shadow: 0 8px 20px rgba(220, 38, 38, 0.3); 
        }}
        
        .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 4px; }}
        .station-name {{ font-weight: bold; font-size: 13.5px; color: var(--station-name-color); line-height: 1.1; }}
        .status-badge {{ font-size: 11px; }}
        
        .card-body-content {{
            display: flex;
            flex-direction: column;
            gap: 4px;
            margin: 4px 0;
        }}
        .row-top, .row-bottom {{
            display: grid;
            gap: 4px;
        }}
        .row-top {{
            grid-template-columns: 1.1fr 1fr 1fr;
        }}
        .row-bottom {{
            grid-template-columns: 1fr 1fr;
        }}
        
        .item-box {{
            background: var(--item-bg);
            padding: 4px 2px;
            border-radius: 6px;
            border: 1px solid var(--item-border);
            text-align: center;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            white-space: nowrap;
            color: var(--item-text);
        }}
        .temp-box {{
            font-size: 0.85em;
            font-weight: 800;
            color: var(--item-text);
        }}
        
        .card-footer-info {{ display: flex; justify-content: space-between; align-items: center; margin-top: 2px; border-top: 1px solid var(--footer-border); padding-top: 3px; }}
        .time {{ font-size: 0.68em; color: var(--time-color); }}
        .click-text {{ font-size: 0.68em; color: var(--click-color); font-weight: bold; font-style: italic; }}
        
        .footer-dev {{ background: linear-gradient(135deg, #0f2942, #1e3a8a); color: #f8fafc; text-align: center; font-weight: 600; padding: 10px 24px; border-radius: 30px; margin: 30px auto 15px auto; display: table; font-size: 13px; box-shadow: 0 4px 12px rgba(15, 41, 66, 0.2); border: 1px solid rgba(255,255,255,0.15); }}
        
        .floating-controls {{
            position: fixed;
            top: 10px;
            right: 10px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            z-index: 1000;
        }}

        .icon-btn {{
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: var(--summary-bg);
            color: var(--text-color);
            border: 1px solid var(--summary-border);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 14px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15);
            transition: all 0.2s ease;
            padding: 0;
        }}
        
        .icon-btn:hover {{
            transform: scale(1.1);
        }}

        .wind-unit-btn {{
            font-size: 11px;
            font-weight: 800;
            letter-spacing: -0.5px;
        }}

        @media (max-width: 600px) {{
            h1 {{
                padding-right: 45px;
                font-size: 19px;
            }}
            .floating-controls {{
                top: 8px;
                right: 8px;
            }}
            .icon-btn {{
                width: 32px;
                height: 32px;
                font-size: 12px;
            }}
            .wind-unit-btn {{
                font-size: 10px;
            }}
        }}
    </style>
</head>
<body class="{alerta_class}">
    <div class="floating-controls">
        <button class="icon-btn" onclick="toggleDarkMode()" id="darkModeBtn" title="Cambiar Modo Oscuro/Claro">🌙</button>
        <button class="icon-btn wind-unit-btn" onclick="toggleWindUnit()" id="windUnitBtn" title="Cambiar Unidad de Viento">kt</button>
    </div>
    <h1>Monitor de Estaciones Automáticas</h1>
    <div class="subtitle-line2">Centro Zonal de Meteorología Marina de Punta Arenas</div>
    <div class="subtitle">Última verificación: {hora_actual_chile} (Tolerancia: {TOLERANCIA_MINUTOS} min)</div>
    {alerta_banner}
    <div class="summary">Estaciones Operativas: {operativas} de {total_estaciones}</div>
    <div id="map"></div>
    <div class="grid">
        {cards_html}
    </div>
    <div style="text-align: center;">
        <div class="footer-dev">Desarrollado por Sgto 2° (Met.) Luis Diego Achurra Garcés</div>
    </div>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        var map = L.map('map').setView([-52.5, -71.0], 6);
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            maxZoom: 12, attribution: '© OpenStreetMap contributors'
        }}).addTo(map);
        {markers_js}

        function toggleDarkMode() {{
            document.body.classList.toggle('dark-mode');
            const isDark = document.body.classList.contains('dark-mode');
            localStorage.setItem('darkModePtaArenas', isDark ? 'enabled' : 'disabled');
            updateButtonText(isDark);
        }}

        function updateButtonText(isDark) {{
            const btn = document.getElementById('darkModeBtn');
            if (btn) btn.innerHTML = isDark ? '☀️' : '🌙';
        }}

        if (localStorage.getItem('darkModePtaArenas') === 'enabled') {{
            document.body.classList.add('dark-mode');
            updateButtonText(true);
        }}

        let windInKnots = true;

        function toggleWindUnit() {{
            windInKnots = !windInKnots;
            localStorage.setItem('windUnitPtaArenas', windInKnots ? 'kt' : 'khr');
            updateWindDisplay();
        }}

        function updateWindDisplay() {{
            const btn = document.getElementById('windUnitBtn');
            if (btn) btn.innerHTML = windInKnots ? 'kt' : 'kmh';

            const itemBoxes = document.querySelectorAll('.card-body-content .item-box');
            itemBoxes.forEach(box => {{
                let text = box.innerHTML;
                if (text.includes('kt') || text.includes('k/hr')) {{
                    box.innerHTML = text.replace(/([\d.,]+)\s*(kt|k\/hr)/gi, (match, p1, p2) => {{
                        let num = parseFloat(p1.replace(',', '.'));
                        if (isNaN(num)) return match;
                        if (!windInKnots && p2.toLowerCase() === 'kt') {{
                            let converted = (num * 1.852).toFixed(1).replace('.', ',');
                            return `${{converted}} k/hr`;
                        }} else if (windInKnots && p2.toLowerCase() !== 'kt') {{
                            let converted = (num / 1.852).toFixed(1).replace('.', ',');
                            return `${{converted}} kt`;
                        }}
                        return match;
                    }});
                }}
            }});
        }}

        if (localStorage.getItem('windUnitPtaArenas') === 'khr') {{
            windInKnots = false;
            setTimeout(updateWindDisplay, 100);
        }}
    </script>
</body>
</html>"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("✓ index.html de Punta Arenas actualizado correctamente.")

def ejecutar_monitoreo():
    print(f"\n--- [{obtener_hora_chile().strftime('%H:%M:%S')}] Verificando estaciones de Punta Arenas ---")
    resultados_dict = {}
    hubo_fallas = False

    for est in ESTACIONES_DIRECTEMAR:
        ok, estado, ultimo, temp, pres, viento, dir_viento, racha, precipitacion = consultar_directemar(est)
        if not ok: hubo_fallas = True
        resultados_dict[est["nombre"]] = {
            "nombre": est["nombre"], "url": est["url"], "lat": est["lat"], "lon": est["lon"],
            "ok": ok, "estado": estado, "ultimo": ultimo, "temp": temp, "pres": pres,
            "viento": viento, "dir_viento": dir_viento, "racha": racha, "precipitacion": precipitacion
        }

    for est in ESTACIONES_EXTERNAS:
        ok, estado, ultimo, temp, pres, viento, dir_viento, racha, precipitacion = consultar_asmar_magallanes(est)
        if not ok: hubo_fallas = True
        resultados_dict[est["nombre"]] = {
            "nombre": est["nombre"], "url": est["url"], "lat": est["lat"], "lon": est["lon"],
            "ok": ok, "estado": estado, "ultimo": ultimo, "temp": temp, "pres": pres,
            "viento": viento, "dir_viento": dir_viento, "racha": racha, "precipitacion": precipitacion
        }

    resultados_totales = [resultados_dict[nombre] for nombre in ORDEN_ESTACIONES if nombre in resultados_dict]
    
    generar_html(resultados_totales, hubo_fallas)
    subir_a_github()

def subir_a_github():
    try:
        print("Sincronizando cambios con GitHub...")
        subprocess.run(["git", "add", "index.html", ARCHIVO_HISTORIAL], check=True)
        resultado = subprocess.run(["git", "commit", "-m", "Actualizacion estaciones Punta Arenas [skip ci]"], capture_output=True, text=True)
        if resultado.returncode != 0:
            if "nothing to commit" in (resultado.stdout + resultado.stderr).lower():
                print("Sin cambios nuevos para subir.")
                return
        subprocess.run(["git", "push"], check=True)
        print("✓ Sincronización completada con éxito.")
    except subprocess.CalledProcessError as e:
        print(f"Error al sincronizar con Git: {e}")

if __name__ == "__main__":
    ejecutar_monitoreo()
