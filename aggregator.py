"""
Agregador diario de licitaciones de defensa para PYME.

Lee config.yaml, consulta las fuentes activas, filtra por las capacidades
de la empresa y genera un feed RSS de salida.

Uso:
    python aggregator.py
    python aggregator.py --config otra_config.yaml
"""
from __future__ import annotations
import argparse
import logging
import os

import yaml
from feedgen.feed import FeedGenerator

from matcher import (
    LONGITUD_MAX_ACRONIMO_POR_DEFECTO,
    construir_indice_palabras,
    capacidades_coincidentes,
)
from sources import placsp, ted

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("agregador")


def cargar_config(ruta: str) -> dict:
    with open(ruta, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def recopilar_items(config: dict) -> list[dict]:
    """Llama a cada fuente activa en config['fuentes'] y junta los resultados."""
    items: list[dict] = []
    fuentes_cfg = config.get("fuentes", {})

    if fuentes_cfg.get("placsp", {}).get("activo"):
        logger.info("Consultando PLACSP...")
        items.extend(placsp.obtener_licitaciones(fuentes_cfg["placsp"]))

    if fuentes_cfg.get("ted", {}).get("activo"):
        logger.info("Consultando TED...")
        cpv_global = sorted({
            cpv
            for cap in config.get("capacidades", {}).values()
            for cpv in cap.get("cpv_orientativos", [])
        })
        items.extend(ted.obtener_licitaciones(fuentes_cfg["ted"], cpv_global))

    return items


def filtrar_por_capacidades(items: list[dict], config: dict) -> list[dict]:
    """Se queda solo con los items cuyo título/resumen casa con alguna capacidad."""
    capacidades = config.get("capacidades", {})
    indice = construir_indice_palabras(capacidades)
    etiquetas = {cap_id: datos["etiqueta"] for cap_id, datos in capacidades.items()}

    longitud_max_acronimo = config.get("filtrado", {}).get(
        "longitud_max_acronimo", LONGITUD_MAX_ACRONIMO_POR_DEFECTO
    )

    filtrados = []
    for item in items:
        texto = f"{item.get('titulo', '')} {item.get('resumen', '')}"
        coincidencias = capacidades_coincidentes(
            texto, indice, longitud_max_acronimo=longitud_max_acronimo
        )
        if coincidencias:
            item["capacidades_detectadas"] = [etiquetas.get(c, c) for c in coincidencias]
            filtrados.append(item)
    return filtrados


def filtrar_por_importe(items: list[dict], config: dict) -> list[dict]:
    """
    Descarta licitaciones por encima de un importe máximo configurable
    (filtrado.importe_maximo, en EUR), pensado para acercar el feed a
    oportunidades accesibles para PYME (tier-2).

    Solo se aplica a los items que tienen campo `importe` (p. ej. PLACSP);
    los que no lo traen (p. ej. TED, cuyo valor no expone la API de
    búsqueda) se conservan siempre.
    """
    importe_maximo = config.get("filtrado", {}).get("importe_maximo")
    if importe_maximo is None:
        return items

    conservados = []
    descartados = 0
    for item in items:
        importe = item.get("importe")
        if importe is not None and importe > importe_maximo:
            descartados += 1
            continue
        conservados.append(item)

    if descartados:
        logger.info(
            "Filtro por importe máximo (%s EUR): descartados %d items por encima del umbral.",
            importe_maximo,
            descartados,
        )
    return conservados


def deduplicar(items: list[dict]) -> list[dict]:
    """Quita duplicados por id (o enlace si no hay id)."""
    vistos = set()
    unicos = []
    for item in items:
        clave = item.get("id") or item.get("enlace")
        if clave and clave not in vistos:
            vistos.add(clave)
            unicos.append(item)
        elif not clave:
            unicos.append(item)  # sin clave fiable: se conserva por seguridad
    return unicos


def generar_rss(items: list[dict], config: dict) -> str:
    """Genera el fichero RSS de salida y devuelve la ruta escrita."""
    salida_cfg = config.get("salida", {})
    fg = FeedGenerator()
    fg.title(salida_cfg.get("titulo_feed", "Licitaciones de Defensa"))
    fg.link(href="https://example.local/licitaciones-defensa", rel="alternate")
    fg.description("Licitaciones de defensa filtradas por capacidades de la empresa")
    fg.language(config.get("empresa", {}).get("idioma_preferente", "es"))

    max_items = salida_cfg.get("max_items", 60)
    for item in items[:max_items]:
        fe = fg.add_entry()
        identificador = item.get("id") or item.get("enlace") or item.get("titulo", "")
        fe.id(identificador)
        titulo = item.get("titulo") or "(sin título)"
        fe.title(f"[{item.get('fuente', '?')}] {titulo}")

        capacidades = ", ".join(item.get("capacidades_detectadas", []))
        descripcion = item.get("resumen", "") or ""
        if capacidades:
            descripcion = f"Capacidades detectadas: {capacidades}<br/>{descripcion}"
        fe.description(descripcion)

        if item.get("enlace"):
            fe.link(href=item["enlace"])

    ruta_salida = salida_cfg.get("fichero", "output/licitaciones_defensa.xml")
    directorio = os.path.dirname(ruta_salida)
    if directorio:
        os.makedirs(directorio, exist_ok=True)
    fg.rss_file(ruta_salida)

    logger.info("Feed generado en %s (%d elementos)", ruta_salida, min(len(items), max_items))
    return ruta_salida


def main():
    parser = argparse.ArgumentParser(description="Agregador de licitaciones de defensa")
    parser.add_argument("--config", default="config.yaml", help="Ruta al fichero de configuración")
    args = parser.parse_args()

    config = cargar_config(args.config)

    items = recopilar_items(config)
    logger.info("Total items recogidos (sin filtrar): %d", len(items))

    items = deduplicar(items)
    items = filtrar_por_capacidades(items, config)
    logger.info("Total items tras filtrar por capacidades: %d", len(items))
    items = filtrar_por_importe(items, config)
    logger.info("Total items tras filtrar por importe máximo: %d", len(items))

    generar_rss(items, config)


if __name__ == "__main__":
    main()
