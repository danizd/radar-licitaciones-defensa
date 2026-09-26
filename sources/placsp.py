"""
Fuente: PLACSP (Plataforma de Contratación del Sector Público, España).

Sindicación Atom oficial, se actualiza a diario y no requiere autenticación.
Referencia oficial:
  https://www.hacienda.gob.es/es-ES/GobiernoAbierto/Datos%20Abiertos/Paginas/licitaciones_plataforma_contratacion.aspx

El feed usa Atom 1.0 con extensiones CODICE; aquí solo extraemos los campos
estándar de Atom (title, summary, link, updated, id), que son suficientes
para el filtrado por palabras clave. El campo `summary` suele traer texto
con este patrón:
  "Id licitación: X; Órgano de Contratación: Y; Importe: Z EUR; Estado: W"
"""
from __future__ import annotations
import logging
import re

import feedparser
import requests

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "radar-pyme-defensa/1.0 (uso propio, no comercial)"}
TIMEOUT_SEGUNDOS = 30

# El summary trae algo como "Importe: 34710.75 EUR".
PATRON_IMPORTE = re.compile(r"Importe:\s*([\d.,]+)\s*EUR", re.IGNORECASE)


def _parsear_importe(summary: str) -> float | None:
    """Extrae el importe en EUR del summary de PLACSP (None si no aparece).

    PLACSP usa el punto como separador decimal ("850000.00", "34710.75").
    Solo si aparece una coma se interpreta como formato europeo
    ("1.234.567,89" -> 1234567.89).
    """
    match = PATRON_IMPORTE.search(summary or "")
    if not match:
        return None
    texto = match.group(1)
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def obtener_licitaciones(config_fuente: dict) -> list[dict]:
    """
    Descarga y parsea el feed Atom de PLACSP.

    Devuelve una lista de dicts normalizados:
        {id, titulo, resumen, enlace, actualizado, fuente}
    Si falla la descarga, devuelve lista vacía y registra un warning
    (no lanza excepción, para no tumbar el resto del agregador).
    """
    url = config_fuente.get("url_atom", "")
    resultados: list[dict] = []

    if not url:
        logger.warning("PLACSP: no hay url_atom configurada, se omite la fuente.")
        return resultados

    try:
        respuesta = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SEGUNDOS)
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("PLACSP: no se pudo descargar %s (%s)", url, exc)
        return resultados

    return parsear_atom(respuesta.content)


def parsear_atom(contenido_bytes: bytes) -> list[dict]:
    """Parseo separado de la descarga, para poder testear con fixtures locales."""
    resultados: list[dict] = []
    feed = feedparser.parse(contenido_bytes)

    if feed.bozo:
        logger.info(
            "PLACSP: aviso del parser (bozo=%s: %s); se continúa con lo que se haya podido leer.",
            feed.bozo,
            getattr(feed, "bozo_exception", ""),
        )

    for entrada in feed.entries:
        summary = entrada.get("summary", "")
        resultados.append({
            "id": entrada.get("id") or entrada.get("link", ""),
            "titulo": entrada.get("title", "(sin título)"),
            "resumen": summary,
            "enlace": entrada.get("link", ""),
            "actualizado": entrada.get("updated", ""),
            "fuente": "PLACSP",
            "importe": _parsear_importe(summary),
        })

    return resultados
