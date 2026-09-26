"""
Fuente: TED (Tenders Electronic Daily), API oficial v3 de la UE.

La búsqueda de anuncios ya publicados no requiere clave de API.
Documentación oficial: https://docs.ted.europa.eu/api/latest/
Explorador de endpoints (Swagger): https://api.ted.europa.eu/swagger

IMPORTANTE - revisar antes de confiar el sistema a producción:
La API de TED usa nombres de campo estilo eForms (p. ej. "classification-cpv",
"buyer-country") y devuelve algunos campos de texto como diccionarios
multilingües ({"es": "...", "en": "..."}). El código de abajo está escrito
de forma defensiva (con .get() y fallbacks) precisamente porque el esquema
exacto de la respuesta puede variar entre versiones de la API; si algo deja
de encajar, compara contra el Swagger antes de asumir que es un bug de este
script.
"""
from __future__ import annotations
import logging
from datetime import date, timedelta

import requests

logger = logging.getLogger(__name__)

ENDPOINT_POR_DEFECTO = "https://api.ted.europa.eu/v3/notices/search"
TIMEOUT_SEGUNDOS = 30

CAMPOS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "classification-cpv",
    "deadline",
    "publication-date",
]


def _fecha_desde(dias_hacia_atras: int) -> str:
    fecha = date.today() - timedelta(days=dias_hacia_atras)
    return fecha.strftime("%Y%m%d")


def _construir_query(paises: list[str], dias_hacia_atras: int, cpv_codes: list[str]) -> str:
    partes = []

    if paises:
        expr_paises = " OR ".join(f"buyer-country={p}" for p in paises)
        partes.append(f"({expr_paises})")

    if cpv_codes:
        expr_cpv = " OR ".join(f"classification-cpv={c}" for c in cpv_codes)
        partes.append(f"({expr_cpv})")

    partes.append(f"publication-date>={_fecha_desde(dias_hacia_atras)}")

    return " AND ".join(partes)


def _valor_multilingue(campo, idioma_preferente: str = "es") -> str:
    """
    Extrae el texto de un campo multilingüe de TED.

    La API devuelve los campos de texto como dicts con claves ISO 639-2
    de 3 letras (p. ej. {"spa": "...", "hun": "...", "eng": "..."}),
    aunque también pueden aparecer claves ISO 639-1 de 2 letras ("es",
    "en"). Se devuelve el texto en español si existe; si no, en inglés;
    y si no, en cualquier idioma disponible (antes de esto se caía al
    primer valor del dict, que solía ser húngaro).

    Algunos campos (p. ej. buyer-name) llegan como {'spa': ['valor']}:
    en ese caso se devuelve el primer elemento de la lista.
    """
    if not isinstance(campo, dict):
        return campo or ""

    def _texto(valor) -> str:
        if isinstance(valor, list):
            return ", ".join(str(v) for v in valor if v)
        return valor or ""

    # Preferencias en orden (sin duplicados): el código configurado ("es"),
    # sus variantes ISO 639-2 ("spa"), inglés, y por último cualquier idioma.
    preferidos = list(dict.fromkeys([idioma_preferente, "spa", "es", "eng", "en"]))
    for clave in preferidos:
        texto = _texto(campo.get(clave))
        if texto:
            return texto

    # Sin español ni inglés: primer valor no vacío en cualquier idioma.
    for valor in campo.values():
        texto = _texto(valor)
        if texto:
            return texto
    return ""


def obtener_licitaciones(config_fuente: dict, cpv_codes: list[str]) -> list[dict]:
    """
    Consulta la API de búsqueda de TED (con paginación automática).

    Si limite_resultados > 100, pagina con iterationNextToken hasta cubrir
    el límite o agotar resultados (la API devuelve máx. ~100 por página).

    Devuelve una lista de dicts normalizados:
        {id, titulo, resumen, enlace, actualizado, fuente}
    Si falla la petición (red, esquema inesperado...) devuelve lista vacía
    y registra un warning, sin tumbar el resto del agregador.
    """
    endpoint = config_fuente.get("endpoint", ENDPOINT_POR_DEFECTO)
    paises = config_fuente.get("paises", [])
    dias = config_fuente.get("dias_hacia_atras", 3)
    limite_total = config_fuente.get("limite_resultados", 100)

    query = _construir_query(paises, dias, cpv_codes)

    todas: list[dict] = []
    token: str | None = None

    while len(todas) < limite_total:
        restantes = limite_total - len(todas)
        # La API limita cada página a ~100; pedimos como mucho lo que falta.
        limite_pagina = min(restantes, 100)
        payload: dict = {
            "query": query,
            "fields": CAMPOS,
            "limit": limite_pagina,
            "scope": "ACTIVE",
            "paginationMode": "ITERATION",
        }
        if token:
            payload["iterationNextToken"] = token

        try:
            respuesta = requests.post(endpoint, json=payload, timeout=TIMEOUT_SEGUNDOS)
            respuesta.raise_for_status()
            datos = respuesta.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("TED: no se pudo consultar la API (%s)", exc)
            # Si ya tenemos algo, devolvemos lo conseguido; si no, lista vacía.
            return _normalizar_respuesta({"notices": todas}) if todas else []

        anuncios = datos.get("notices") or datos.get("results") or []
        if not anuncios:
            break
        todas.extend(anuncios)

        token = datos.get("iterationNextToken")
        total = datos.get("totalNoticeCount")
        if not token or (total is not None and len(todas) >= total):
            break
        if len(anuncios) < limite_pagina:
            break

    # Normalizamos a través del mismo helper (esperando clave "notices").
    return _normalizar_respuesta({"notices": todas})


def _normalizar_respuesta(datos: dict) -> list[dict]:
    # El nombre exacto de la clave con la lista de anuncios puede variar
    # según la versión de la API; se prueban las variantes documentadas.
    anuncios = datos.get("notices") or datos.get("results") or []

    resultados = []
    for anuncio in anuncios:
        num_publicacion = anuncio.get("publication-number", "")
        resultados.append({
            "id": num_publicacion or anuncio.get("buyer-name", ""),
            "titulo": _valor_multilingue(anuncio.get("notice-title")),
            "resumen": _valor_multilingue(anuncio.get("buyer-name")),
            "enlace": f"https://ted.europa.eu/en/notice/-/detail/{num_publicacion}"
                      if num_publicacion else "",
            "actualizado": anuncio.get("publication-date", ""),
            "fuente": "TED",
        })
    return resultados
