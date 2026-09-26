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
        {capacidad_id: [palabra_normalizada, ...]}
    """
    indice = {}
    for cap_id, datos in (capacidades or {}).items():
        palabras = datos.get("palabras_clave", []) or []
        indice[cap_id] = [_normalizar(p) for p in palabras if p]
    return indice


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

    `longitud_max_acronimo` se configura en config.yaml
    (filtrado.longitud_max_acronimo); si no se pasa, se usa el valor
    por defecto.
    """
    texto_norm = _normalizar(texto)
    if not texto_norm:
        return []

    coincidencias = []
    for cap_id, palabras in indice_palabras.items():
        for palabra in palabras:
            if not palabra:
                continue
            if len(palabra) <= longitud_max_acronimo:
                coincide = _coincide_como_palabra_completa(palabra, texto_norm)
            else:
                coincide = palabra in texto_norm
            if coincide:
                coincidencias.append(cap_id)
                break
    return coincidencias
