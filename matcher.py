"""
Coincidencia de texto contra las capacidades definidas en config.yaml.

La comparación ignora mayúsculas/minúsculas y acentos, para que
"vetrónica" y "VETRONICA" (o "vetronica") se traten igual.

Los acrónimos cortos (3-4 letras, p. ej. "UAS", "SOC", "UAV") solo
coinciden como PALABRA COMPLETA (con límites de palabra): así "UAS" no
aparece dentro de "aguas" ni "SOC" dentro de "sociedad"/"asociación".
Las palabras clave más largas (p. ej. "cloud computing", "vehículos
blindados") siguen coincidiendo como subcadena.
"""
from __future__ import annotations
import re
import unicodedata

# Valor por defecto: las palabras clave de longitud <= a este valor se
# consideran acrónimos y se buscan como palabra completa (con límites de
# palabra). Se puede sobreescribir en config.yaml -> filtrado ->
# longitud_max_acronimo.
LONGITUD_MAX_ACRONIMO_POR_DEFECTO = 4


def _normalizar(texto: str) -> str:
    """Minúsculas y sin diacríticos (tildes, diéresis...)."""
    if not texto:
        return ""
    texto = texto.lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


def _coincide_como_palabra_completa(palabra: str, texto_normalizado: str) -> bool:
    """
    True si `palabra` aparece en el texto como palabra completa.

    Se tolera el plural inglés típico de acrónimos ("UAV" -> "UAVs") sin
    reabrir los falsos positivos: "uas" sigue sin aparecer en "aguas"
    porque la "u" no empieza palabra.
    """
    return re.search(rf"\b{re.escape(palabra)}s?\b", texto_normalizado) is not None


def construir_indice_palabras(capacidades: dict) -> dict:
    """
    A partir del bloque `capacidades` del config, devuelve:
        {capacidad_id: {"palabras": [...], "obligatorias": [...]}}

    - palabras: `palabras_clave` normalizadas (coincide la capacidad si
      casa AL MENOS UNA).
    - obligatorias: `palabras_obligatorias` normalizadas (opcional). Si
      la lista no está vacía, además debe casar AL MENOS UNA de ellas:
      condición AND. Sirve para acotar términos ruidosos ("streaming",
      "vídeo") a un contexto concreto ("dron", "UAV"...).
    """
    indice = {}
    for cap_id, datos in (capacidades or {}).items():
        palabras = [_normalizar(p) for p in (datos.get("palabras_clave") or []) if p]
        obligatorias = [_normalizar(p) for p in (datos.get("palabras_obligatorias") or []) if p]
        indice[cap_id] = {"palabras": palabras, "obligatorias": obligatorias}
    return indice


def _coincide_palabra(palabra: str, texto_normalizado: str, longitud_max_acronimo: int) -> bool:
    """Aplica la regla de coincidencia de una palabra clave normalizada."""
    if len(palabra) <= longitud_max_acronimo:
        return _coincide_como_palabra_completa(palabra, texto_normalizado)
    return palabra in texto_normalizado


def capacidades_coincidentes(
    texto: str,
    indice_palabras: dict,
    longitud_max_acronimo: int = LONGITUD_MAX_ACRONIMO_POR_DEFECTO,
) -> list[str]:
    """
    Devuelve la lista de ids de capacidad cuyas palabras clave aparecen
    dentro de `texto`.

    - Acrónimos cortos (<= `longitud_max_acronimo` letras): se exige que
      aparezcan como palabra completa (límites de palabra), para no dar
      falsos positivos ("UAS" dentro de "aguas", "SOC" dentro de "sociedad").
    - Palabras clave más largas: coincidencia por subcadena (tras
      normalizar), como antes.
    - Si la capacidad define `palabras_obligatorias`, además de casar una
      `palabra_clave` debe casar al menos una obligatoria (condición AND);
      aplica la misma regla de acrónimos que las palabras clave.
      (Compatibilidad: se acepta también el formato antiguo de lista plana
      de palabras, que se comporta como OR puro.)

    `longitud_max_acronimo` se configura en config.yaml
    (filtrado.longitud_max_acronimo); si no se pasa, se usa el valor
    por defecto.
    """
    texto_norm = _normalizar(texto)
    if not texto_norm:
        return []

    coincidencias = []
    for cap_id, datos in indice_palabras.items():
        if isinstance(datos, dict):
            palabras = datos.get("palabras") or []
            obligatorias = datos.get("obligatorias") or []
        else:
            palabras, obligatorias = datos or [], []

        if not palabras:
            continue  # sin palabras_clave la capacidad no puede activarse

        coincide = any(
            _coincide_palabra(p, texto_norm, longitud_max_acronimo)
            for p in palabras
            if p
        )
        if coincide and obligatorias:
            coincide = any(
                _coincide_palabra(p, texto_norm, longitud_max_acronimo)
                for p in obligatorias
                if p
            )
        if coincide:
            coincidencias.append(cap_id)
    return coincidencias
